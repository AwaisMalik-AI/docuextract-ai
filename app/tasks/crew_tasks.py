from app.services.extraction_crew import ExtractionCrew
from app.tasks.celery_app import celery


@celery.task(name="docuextract.run_extraction_crew")
def run_extraction_crew_task(text: str, doc_type: str = "invoice") -> dict:
    result = ExtractionCrew().run(text, doc_type)
    return {
        "crew": result.crew,
        "used_llm": result.used_llm,
        "steps": result.steps,
        "fields": result.fields,
        "confidence": result.confidence,
    }
