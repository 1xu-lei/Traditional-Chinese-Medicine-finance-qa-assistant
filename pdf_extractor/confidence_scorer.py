# -*- coding: utf-8 -*-
"""confidence_scorer.py: 综合置信度评分"""
def P(source):
    return {"pdfplumber_standard": 95, "pdfplumber_density": 75, "llm_fallback": 50}.get(source, 30)
def F(conf):
    return 95 if conf >= 80 else 70 if conf >= 60 else 50 if conf >= 40 else 30
def V(count):
    return min(50 + (25 if count >= 3 else 0) + (15 if count >= 5 else 0) + (10 if count >= 8 else 0), 100)
def C(p, t):
    return round(p / max(t, 1) * 100)
def calc(p, f, v, c):
    return round(0.25 * p + 0.25 * f + 0.20 * v + 0.30 * c, 1)
def decide(conf):
    if conf >= 80: return "auto", "auto"
    if conf >= 60: return "flagged", "flagged"
    return "rejected", "rejected"
