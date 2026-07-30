#!/usr/bin/env python3
"""Offline ONNX embedding worker for multilingual-e5-small."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from semantic_runtime_contract import CONTRACT_PATH, load_contract


def mean_pool(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    mask = attention_mask[..., None].astype(np.float32)
    summed = (last_hidden_state * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), 1e-9, None)
    pooled = summed / counts
    norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return (pooled / norms).astype(np.float32)


def embed(
    model_dir: Path,
    texts: list[str],
    prefix: str,
    batch_size: int,
    *,
    model_file: str,
    max_length: int,
) -> np.ndarray:
    tokenizer = AutoTokenizer.from_pretrained(
        model_dir,
        local_files_only=True,
        trust_remote_code=False,
    )
    session = ort.InferenceSession(
        str(model_dir / model_file),
        providers=["CPUExecutionProvider"],
    )
    input_names = {item.name for item in session.get_inputs()}
    batches = []
    for start in range(0, len(texts), batch_size):
        batch = [prefix + text for text in texts[start:start + batch_size]]
        encoded = tokenizer(
            batch,
            max_length=max_length,
            padding=True,
            truncation=True,
            return_tensors="np",
        )
        inputs = {
            name: encoded[name].astype(np.int64)
            for name in input_names
            if name in encoded
        }
        if "token_type_ids" in input_names and "token_type_ids" not in inputs:
            inputs["token_type_ids"] = np.zeros_like(inputs["input_ids"], dtype=np.int64)
        output = session.run(None, inputs)[0]
        batches.append(mean_pool(output, encoded["attention_mask"]))
    return np.concatenate(batches, axis=0) if batches else np.empty((0, 384), dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prefix", choices=["query", "passage"], required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--matrix")
    parser.add_argument("--contract", default=str(CONTRACT_PATH))
    args = parser.parse_args()
    contract = load_contract(args.contract)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    texts = payload.get("texts")
    if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
        raise ValueError("Input must contain a string list named texts")
    vectors = embed(
        Path(args.model_dir).resolve(),
        texts,
        contract["embedding"][f"{args.prefix}_prefix"],
        max(1, args.batch_size),
        model_file=next(
            item["path"]
            for item in contract["model"]["artifacts"]
            if item["source"].endswith(".onnx")
        ),
        max_length=contract["embedding"]["max_length"],
    )
    output = Path(args.output)
    if args.matrix:
        if vectors.shape[0] != 1:
            raise ValueError("Matrix scoring requires exactly one query")
        matrix = np.load(Path(args.matrix), mmap_mode="r", allow_pickle=False)
        scores = matrix @ vectors[0]
        output.write_text(
            json.dumps({"scores": scores.astype(float).tolist()}),
            encoding="utf-8",
        )
    else:
        np.save(output, vectors, allow_pickle=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
