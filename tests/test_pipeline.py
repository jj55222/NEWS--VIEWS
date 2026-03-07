import unittest

from src.ingest.curated_ingest import ingest_curated_sources
from src.normalize.normalizer import normalize_candidate
from src.score.scorer import score_incident


class PipelineTest(unittest.TestCase):
    def test_ingest_normalize_score_path(self):
        items = [
            {
                "source_type": "youtube",
                "source_url": "https://example.com/test",
                "source_title": "Bodycam release",
                "channel_or_publisher": "Agency",
                "publish_date": "2024-01-01",
                "hints": {
                    "agency": "Agency",
                    "incident_date": "2023-12-01",
                    "location": "Somewhere",
                    "people": ["A"],
                    "incident_type": "use of force",
                    "narrative_hook": "Disputed timeline",
                },
            }
        ]

        candidates = ingest_curated_sources(items)
        self.assertEqual(len(candidates), 1)

        incident, confidence = normalize_candidate(candidates[0])
        self.assertTrue(incident.incident_id.startswith("INC-"))

        breakdown = score_incident(incident, confidence)
        self.assertGreater(incident.story_value_score, 0)
        self.assertIn("story_value", breakdown)
        self.assertIn(incident.routing_status, {"ROUTE_PACKET", "ROUTE_ENRICH", "ROUTE_HOLD"})


if __name__ == "__main__":
    unittest.main()
