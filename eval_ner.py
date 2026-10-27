import json, os, random, numpy as np, torch
from transformers import BertTokenizerFast, BertForTokenClassification
from seqeval.metrics import classification_report

LABELS = ["O","B-COMP","I-COMP","B-METRIC","I-METRIC","B-PERIOD","I-PERIOD"]
LABEL2ID = {l:i for i,l in enumerate(LABELS)}
ID2LABEL = {i:l for i,l in enumerate(LABELS)}

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")

tokenizer = BertTokenizerFast.from_pretrained("bert-base-chinese")
model = BertForTokenClassification.from_pretrained("models/ner_model/final").to(device)
model.eval()
print("Model loaded")

with open("data/train_data.json", "r", encoding="utf-8") as f:
    all_data = json.load(f)
random.shuffle(all_data)
split = int(len(all_data) * 0.9)
test_data = all_data[split:]
print(f"Test: {len(test_data)} samples")

def predict(tokens):
    encoding = tokenizer(tokens, truncation=True, is_split_into_words=True, max_length=128, padding="max_length", return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(input_ids=encoding["input_ids"], attention_mask=encoding["attention_mask"])
    preds = outputs.logits.argmax(dim=-1)[0].cpu().numpy()
    word_ids = encoding.word_ids(0)
    result = []
    prev = None
    for wid, p in zip(word_ids, preds):
        if wid is None or wid == prev: continue
        result.append(ID2LABEL.get(int(p), "O"))
        prev = wid
    return result

true_labels, pred_labels = [], []
errors = []

for idx, item in enumerate(test_data):
    tokens = item["tokens"]
    gold = item["labels"]
    pred = predict(tokens)
    min_len = min(len(gold), len(pred))
    true_labels.append(gold[:min_len])
    pred_labels.append(pred[:min_len])
    if gold != pred[:len(gold)] and len(errors) < 20:
        tok_str = "".join(tokens[:30])
        gold_str = " ".join([f"{t}/{l}" for t,l in zip(tokens[:15], gold[:15])])
        pred_str = " ".join([f"{t}/{l}" for t,l in zip(tokens[:15], pred[:15])])
        errors.append(f"#{idx}: {tok_str}\n  GOLD: {gold_str}\n  PRED: {pred_str}")

print("\n=== 评估指标 ===")
report = classification_report(true_labels, pred_labels, output_dict=True)
for label, metrics in report.items():
    if isinstance(metrics, dict):
        print(f"  {label:10s} P={metrics['precision']:.4f} R={metrics['recall']:.4f} F1={metrics['f1-score']:.4f}  support={int(metrics['support'])}")
    else:
        print(f"  {label}: {metrics:.4f}")

print(f"\n=== 错误样例 (前10) ===")
for e in errors[:10]:
    print(e + "\n")

print("\n=== 典型问题测试 ===")
tests = [
    "同仁堂2025利润是多少",
    "金花股份2023年第三季度净利润",
    "白云山和999对比营业总收入",
    "2025年第三季度的",
    "哪些企业是亏损的",
    "同仁堂资产负债率",
    "华润三九和云南白药哪个净资产收益率高",
]
for q in tests:
    tokens = list(q)
    pred = predict(tokens)
    tagged = " ".join([f"{t}/{l}" for t,l in zip(tokens, pred)])
    slots = {}
    cur_label, cur_text = None, []
    for token, tag in zip(tokens, pred):
        if tag.startswith("B-"):
            if cur_label and cur_text:
                slots.setdefault(cur_label, []).append("".join(cur_text))
            cur_label = tag[2:]; cur_text = [token]
        elif tag.startswith("I-") and cur_label == tag[2:]:
            cur_text.append(token)
        else:
            if cur_label and cur_text:
                slots.setdefault(cur_label, []).append("".join(cur_text))
            cur_label = None; cur_text = []
    if cur_label and cur_text:
        slots.setdefault(cur_label, []).append("".join(cur_text))
    print(f"Q: {q}")
    print(f"  标注: {tagged}")
    print(f"  槽位: {json.dumps(slots, ensure_ascii=False)}")
    print()
