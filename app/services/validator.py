import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, ValidationRule

logger = logging.getLogger(__name__)


class DocumentValidator:
    """Configurable business rules engine for extracted document data."""

    BUILTIN_RULES = [
        {
            "name": "required_invoice_fields",
            "rule_type": "required_field",
            "config": {"fields": ["invoice_number", "invoice_date", "total_amount", "vendor_name"]},
            "severity": "error",
            "doc_types": ["invoice"],
        },
        {
            "name": "total_calculation_check",
            "rule_type": "calculation",
            "config": {"formula": "subtotal + tax_amount == total_amount", "tolerance": 0.02},
            "severity": "warning",
            "doc_types": ["invoice", "receipt"],
        },
        {
            "name": "date_format_check",
            "rule_type": "format",
            "config": {"field": "invoice_date", "pattern": r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\w+ \d{1,2}, \d{4}"},
            "severity": "info",
            "doc_types": None,
        },
        {
            "name": "amount_range_check",
            "rule_type": "range",
            "config": {"field": "total_amount", "min": 0.01, "max": 10_000_000},
            "severity": "warning",
            "doc_types": None,
        },
    ]

    async def validate(self, extracted_data: dict, doc_type: Optional[str], db: AsyncSession) -> dict:
        results = {"passed": [], "warnings": [], "errors": [], "info": []}
        fields = extracted_data.get("fields", {})

        db_rules = await db.execute(select(ValidationRule).where(ValidationRule.is_active == True))
        custom_rules = db_rules.scalars().all()

        all_rules = self.BUILTIN_RULES + [
            {"name": r.name, "rule_type": r.rule_type, "config": r.config, "severity": r.severity, "doc_types": r.doc_types}
            for r in custom_rules
        ]

        for rule in all_rules:
            if rule["doc_types"] and doc_type and doc_type not in rule["doc_types"]:
                continue

            try:
                check_result = self._apply_rule(rule, fields)
                bucket = check_result["severity"] if not check_result["passed"] else "passed"
                results[bucket].append(check_result)
            except Exception as e:
                logger.warning(f"Rule {rule['name']} failed: {e}")

        await self._check_duplicates(fields, db, results)

        results["is_valid"] = len(results["errors"]) == 0
        return results

    def _apply_rule(self, rule: dict, fields: dict) -> dict:
        rule_type = rule["rule_type"]
        config = rule["config"]

        if rule_type == "required_field":
            missing = [f for f in config["fields"] if not self._get_field_value(fields, f)]
            return {
                "rule": rule["name"],
                "passed": len(missing) == 0,
                "severity": rule["severity"],
                "message": f"Missing required fields: {', '.join(missing)}" if missing else "All required fields present",
            }

        elif rule_type == "format":
            value = self._get_field_value(fields, config["field"])
            if not value:
                return {"rule": rule["name"], "passed": True, "severity": "info", "message": f"Field {config['field']} not present, skipped"}
            matched = bool(re.match(config["pattern"], str(value)))
            return {
                "rule": rule["name"],
                "passed": matched,
                "severity": rule["severity"],
                "message": f"Field {config['field']} format {'valid' if matched else 'invalid'}",
            }

        elif rule_type == "range":
            value = self._get_field_value(fields, config["field"])
            if not value:
                return {"rule": rule["name"], "passed": True, "severity": "info", "message": "Field not present"}
            try:
                num_val = float(str(value).replace(",", "").replace("$", ""))
                in_range = config["min"] <= num_val <= config["max"]
                return {
                    "rule": rule["name"],
                    "passed": in_range,
                    "severity": rule["severity"],
                    "message": f"{config['field']}={num_val} {'within' if in_range else 'outside'} range [{config['min']}, {config['max']}]",
                }
            except ValueError:
                return {"rule": rule["name"], "passed": False, "severity": rule["severity"], "message": f"Cannot parse {config['field']} as number"}

        elif rule_type == "calculation":
            return self._check_calculation(rule, fields, config)

        return {"rule": rule["name"], "passed": True, "severity": "info", "message": "Unknown rule type, skipped"}

    def _check_calculation(self, rule: dict, fields: dict, config: dict) -> dict:
        subtotal = self._parse_amount(self._get_field_value(fields, "subtotal"))
        tax = self._parse_amount(self._get_field_value(fields, "tax_amount"))
        total = self._parse_amount(self._get_field_value(fields, "total_amount"))

        if None in (subtotal, tax, total):
            return {"rule": rule["name"], "passed": True, "severity": "info", "message": "Insufficient fields for calculation check"}

        tolerance = config.get("tolerance", 0.02)
        expected = subtotal + tax
        diff = abs(expected - total)
        passed = diff <= (total * tolerance) if total > 0 else diff < 0.01

        return {
            "rule": rule["name"],
            "passed": passed,
            "severity": rule["severity"],
            "message": f"Subtotal({subtotal}) + Tax({tax}) = {expected}, Total = {total}, diff = {diff:.2f}",
        }

    async def _check_duplicates(self, fields: dict, db: AsyncSession, results: dict):
        invoice_num = self._get_field_value(fields, "invoice_number")
        vendor = self._get_field_value(fields, "vendor_name")
        if not invoice_num:
            return

        query = select(Document).where(
            Document.status.notin_(["failed", "rejected"]),
            Document.extracted_data["fields"]["invoice_number"]["value"].as_string() == str(invoice_num),
        )
        existing = await db.execute(query)
        dupes = existing.scalars().all()

        if dupes:
            results["warnings"].append({
                "rule": "duplicate_detection",
                "passed": False,
                "severity": "warning",
                "message": f"Possible duplicate: invoice {invoice_num} found in {len(dupes)} existing document(s)",
            })

    @staticmethod
    def _get_field_value(fields: dict, name: str):
        field = fields.get(name, {})
        if isinstance(field, dict):
            return field.get("value")
        return field

    @staticmethod
    def _parse_amount(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(str(value).replace(",", "").replace("$", "").replace("€", "").replace("£", ""))
        except (ValueError, TypeError):
            return None


def get_validator() -> DocumentValidator:
    return DocumentValidator()
