#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/home/fschulz/dev/training/nanochat")

from nanochat.common import compute_init, compute_cleanup, autodetect_device_type
from nanochat.checkpoint_manager import load_model
from nanochat.engine import Engine


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_f1(pred: str, gold: str) -> float:
    pred_tokens = normalize(pred).split()
    gold_tokens = normalize(gold).split()
    if not pred_tokens or not gold_tokens:
        return 0.0
    gold_counts = defaultdict(int)
    pred_counts = defaultdict(int)
    for token in gold_tokens:
        gold_counts[token] += 1
    for token in pred_tokens:
        pred_counts[token] += 1
    overlap = sum(min(pred_counts[t], gold_counts[t]) for t in pred_counts)
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def exact_match(pred: str, gold: str) -> float:
    return float(normalize(pred) == normalize(gold))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-json", type=Path, required=True)
    parser.add_argument("--model-tag", type=str, required=True)
    parser.add_argument("--step", type=int, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--device-type", type=str, default="")
    parser.add_argument("--max-new-tokens", type=int, default=192)
    parser.add_argument("--logic-extract-max-new-tokens", type=int, default=1024)
    args = parser.parse_args()

    items = json.loads(args.benchmark_json.read_text())
    device_type = autodetect_device_type() if args.device_type == "" else args.device_type
    ddp, ddp_rank, ddp_local_rank, ddp_world_size, device = compute_init(device_type)
    model, tokenizer, meta = load_model("sft", device, phase="eval", model_tag=args.model_tag, step=args.step)
    engine = Engine(model, tokenizer)

    results = []
    agg = defaultdict(list)
    for item in items:
        conversation = {
            "messages": [
                {"role": "user", "content": item["prompt"]},
                {"role": "assistant", "content": ""},
            ]
        }
        encoded = tokenizer.render_for_completion(conversation)
        max_tokens = args.logic_extract_max_new_tokens if item.get("question_type") == "logic_extract" else args.max_new_tokens
        generated, _ = engine.generate_batch(
            encoded,
            num_samples=1,
            max_tokens=max_tokens,
            temperature=0.0,
            top_k=50,
        )
        completion = tokenizer.decode(generated[0][len(encoded):]).strip()
        em = exact_match(completion, item["answer"])
        f1 = token_f1(completion, item["answer"])
        item_name = item.get("artifact_name") or item.get("paper_id") or "item"
        results.append({
            "artifact_name": item_name,
            "question_type": item["question_type"],
            "prompt": item["prompt"],
            "gold_answer": item["answer"],
            "prediction": completion,
            "exact_match": em,
            "token_f1": f1,
        })
        agg[item["question_type"]].append((em, f1))

    summary = {
        "model_tag": args.model_tag,
        "benchmark_json": str(args.benchmark_json),
        "num_items": len(results),
        "overall_exact_match": sum(r["exact_match"] for r in results) / len(results),
        "overall_token_f1": sum(r["token_f1"] for r in results) / len(results),
        "by_question_type": {
            key: {
                "count": len(values),
                "exact_match": sum(v[0] for v in values) / len(values),
                "token_f1": sum(v[1] for v in values) / len(values),
            }
            for key, values in agg.items()
        },
        "results": results,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2))
    compute_cleanup()


if __name__ == "__main__":
    main()
