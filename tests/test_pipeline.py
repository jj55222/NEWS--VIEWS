import json
import tempfile
import unittest
from pathlib import Path

from src.ingest.curated_ingest import ingest_curated_sources
from src.ingest.brave_discovery import discover_candidates_from_brave
from src.normalize.normalizer import normalize_candidate
from src.pipeline import run_pipeline
from src.score.scorer import score_incident


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class PipelineTest(unittest.TestCase):
    def test_ingest_normalize_score_path(self):
        items = [
            {
                "source_url": "https://example.com/test",
                "title": "Bodycam officer pursuit and arrest",
                "description": "Raw bodycam release with incident details",
                "publisher": "Agency",
                "published_date": "2024-01-01",
                "media_type": "video",
                "transcript_available": True,
                "raw_footage_likelihood": 0.9,
                "watermark_likelihood": 0.1,
                "hints": {
                    "agency": "Agency",
                    "date_range": "2023-12-01",
                    "location": "Somewhere",
                    "people": ["A"],
                    "incident_type": "use of force",
                    "allegations_or_charges": ["disputed timeline"],
                    "transcript_search_anchors": ["foot pursuit"],
                },
            }
        ]

        candidate_with_route = ingest_curated_sources(items)
        self.assertEqual(len(candidate_with_route), 1)

        candidate, ingest_decision = candidate_with_route[0]
        self.assertEqual(ingest_decision.status, "ROUTE_ENRICH")

        incident, confidence, normalize_decision = normalize_candidate(candidate)
        self.assertTrue(incident.incident_id.startswith("INC-"))
        self.assertIn(normalize_decision.status, {"NORMALIZED_STRONG", "NORMALIZED_PARTIAL"})

        breakdown, score_decision = score_incident(incident, confidence)
        self.assertGreater(incident.story_value_score, 0)
        self.assertIn("story_value", breakdown)
        self.assertIn(score_decision.status, {"PRIORITY_PACKET", "RESEARCH_PACKET", "WATCHLIST_PACKET", "MANUAL_REVIEW", "ARCHIVE"})

    def test_pipeline_outputs_batch_metrics(self):
        items = [
            {
                "source_url": "https://example.com/test2",
                "title": "Bodycam traffic stop",
                "description": "Deputy bodycam footage",
                "publisher": "County Sheriff",
                "published_date": "2024-02-01",
                "media_type": "video",
                "raw_footage_likelihood": 0.8,
                "watermark_likelihood": 0.2,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.json"
            output_dir = Path(tmp) / "out"
            input_path.write_text(json.dumps(items))

            run_pipeline(str(input_path), str(output_dir))

            self.assertTrue((output_dir / "audit.jsonl").exists())
            self.assertTrue((output_dir / "batch_metrics.json").exists())
            first_incident = (output_dir / "incidents.jsonl").read_text().splitlines()[0]
            parsed = json.loads(first_incident)
            self.assertIn("researchability_score", parsed)
            self.assertIn("routing_status", parsed)


if __name__ == "__main__":
    unittest.main()
