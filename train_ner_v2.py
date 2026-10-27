
# -*- coding: utf-8 -*-
"""??NER???? v2 - ??????????"""
import json, os, random, argparse, torch, numpy as np
from torch.utils.data import Dataset
from transformers import (
    BertTokenizerFast, BertForTokenClassification,
    TrainingArguments, Trainer, set_seed
)

# ????
import transformers
tv = transformers.__version__.split(".")
tv_num = int(tv[0]) * 100 + int(tv[1]) if len(tv) >= 2 else 0
print(f"Transformers version: {transformers.__version__}")

LABELS = ["O","B-COMP","I-COMP","B-METRIC","I-METRIC","B-PERIOD","I-PERIOD"]
LABEL2ID = {l:i for i,l in enumerate(LABELS)}
ID2LABEL = {i:l for i,l in enumerate(LABELS)}
NUM_LABELS = len(LABELS)

set_seed(42)

class NERDataset(Dataset):
    def __init__(self, data, tokenizer, max_len=128):
        self.data = data
        self.tokenizer = tokenizer
        self.max_len = max_len
    def __len__(self):
        return len(self.data)
    def __getitem__(self, idx):
        item = self.data[idx]
        tokens = item["tokens"]
        labels = item["labels"]
        encoding = self.tokenizer(tokens, truncation=True, is_split_into_words=True,
                                   max_length=self.max_len, padding="max_length", return_tensors="pt")
        word_ids = encoding.word_ids(0)
        label_ids = []
        for wid in word_ids:
            if wid is None:
                label_ids.append(-100)
            else:
                label_ids.append(LABEL2ID.get(labels[wid], 0))
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(label_ids, dtype=torch.long),
        }

def train(args):
    print("=" * 50)
    print("NER Training v2")
    print("=" * 50)
    
    # ????
    with open(args.data_path, "r", encoding="utf-8") as f:
        all_data = json.load(f)
    print(f"Loaded {len(all_data)} samples")
    random.shuffle(all_data)
    split = int(len(all_data) * 0.9)
    train_data = all_data[:split]
    val_data = all_data[split:]
    print(f"Train: {len(train_data)}, Val: {len(val_data)}")
    
    # Tokenizer + ???
    tokenizer = BertTokenizerFast.from_pretrained(args.model_name)
    train_ds = NERDataset(train_data, tokenizer, args.max_len)
    val_ds = NERDataset(val_data, tokenizer, args.max_len)
    
    # ??
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    
    model = BertForTokenClassification.from_pretrained(
        args.model_name,
        num_labels=NUM_LABELS,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )
    model.to(device)
    
    # ?????????????????
    w_before = model.classifier.weight.detach().clone()
    
    # ???? - ?????
    extra_args = {}
    if tv_num >= 428:  # 4.28+
        extra_args["evaluation_strategy"] = "epoch"
        extra_args["save_strategy"] = "epoch"
        extra_args["load_best_model_at_end"] = True
        extra_args["metric_for_best_model"] = "eval_loss"
        extra_args["greater_is_better"] = False
    else:
        # ?????????????
        pass
    
    output_dir = os.path.join(args.output_dir, "ner_model")
    training_args = TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size * 2,
        learning_rate=args.lr,
        weight_decay=0.01,
        warmup_ratio=0.1,
        save_total_limit=1,
        logging_steps=50,
        report_to="none",
        **extra_args,
    )
    
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        tokenizer=tokenizer,
    )
    
    # ?????loss
    before_loss = trainer.evaluate()
    print(f"Before training - eval_loss: {before_loss.get('eval_loss', 'N/A')}")
    
    # ????
    trainer.train()
    
    # ???????????
    w_after = model.classifier.weight.detach().clone()
    weight_diff = (w_before - w_after).abs().max().item()
    print(f"Classifier max weight change: {weight_diff:.8f}")
    if weight_diff < 0.0001:
        print("WARNING: Training did NOT update weights! Check optimizer/dataloader.")
    else:
        print("OK: Training updated weights.")
    
    # ??
    final_path = os.path.join(output_dir, "final")
    os.makedirs(final_path, exist_ok=True)
    model.save_pretrained(final_path)
    tokenizer.save_pretrained(final_path)
    
    # ??config??????
    cfg_path = os.path.join(final_path, "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["num_labels"] = NUM_LABELS
    cfg["id2label"] = ID2LABEL
    cfg["label2id"] = LABEL2ID
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    
    # ??????
    with open(os.path.join(final_path, "label_map.json"), "w", encoding="utf-8") as f:
        json.dump({"labels": LABELS, "label2id": LABEL2ID, "id2label": ID2LABEL}, f, ensure_ascii=False)
    
    print(f"Model saved to {final_path}")

def predict(text, model, tokenizer, device):
    tokens = list(text)
    encoding = tokenizer(tokens, truncation=True, is_split_into_words=True,
                         max_length=128, padding="max_length", return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(input_ids=encoding["input_ids"], attention_mask=encoding["attention_mask"])
    preds = outputs.logits.argmax(dim=-1)[0].cpu().numpy()
    word_ids = encoding.word_ids(0)
    result = []
    prev = None
    for wid, p in zip(word_ids, preds):
        if wid is None or wid == prev:
            continue
        result.append((tokens[wid], model.config.id2label.get(int(p), "O")))
        prev = wid
    return result

def extract_slots(tagged):
    slots = {}
    cur_label = None
    cur_text = []
    for token, tag in tagged:
        if tag.startswith("B-"):
            if cur_label and cur_text:
                slots.setdefault(cur_label, []).append("".join(cur_text))
            cur_label = tag[2:]
            cur_text = [token]
        elif tag.startswith("I-") and cur_label == tag[2:]:
            cur_text.append(token)
        else:
            if cur_label and cur_text:
                slots.setdefault(cur_label, []).append("".join(cur_text))
            cur_label = None
            cur_text = []
    if cur_label and cur_text:
        slots.setdefault(cur_label, []).append("".join(cur_text))
    return slots

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_path", default="data/train_data.json")
    parser.add_argument("--output_dir", default="models")
    parser.add_argument("--model_name", default="bert-base-chinese")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch_size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max_len", type=int, default=128)
    parser.add_argument("--predict", type=str, default=None)
    args = parser.parse_args()
    
    if args.predict:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        tokenizer = BertTokenizerFast.from_pretrained(args.model_name)
        model = BertForTokenClassification.from_pretrained(
            os.path.join(args.output_dir, "ner_model", "final")
        ).to(device)
        model.eval()
        tagged = predict(args.predict, model, tokenizer, device)
        print(" ".join([f"{w}/{t}" for w, t in tagged]))
        slots = extract_slots(tagged)
        print("Slots:", json.dumps(slots, ensure_ascii=False))
    else:
        train(args)
