import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class SemanticE5WorkerSourceTest(unittest.TestCase):
    def test_onnx_segment_ids_are_filled_when_tokenizer_omits_them(self):
        source = (ROOT / "scripts" / "semantic_e5_worker.py").read_text(encoding="utf-8")
        self.assertIn('"token_type_ids" in input_names', source)
        self.assertIn('np.zeros_like(inputs["input_ids"]', source)

    def test_worker_forbids_remote_model_code(self):
        source = (ROOT / "scripts" / "semantic_e5_worker.py").read_text(encoding="utf-8")
        self.assertIn("local_files_only=True", source)
        self.assertIn("trust_remote_code=False", source)


if __name__ == "__main__":
    unittest.main()
