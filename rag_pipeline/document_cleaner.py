# -*- coding: utf-8 -*-
"""
文档清洗器
"""
import re
from config import FOOTER_KEYWORDS_BASE

def clean_latex(text):
    def _fix_math(m):
        inner = m.group(1)
        inner = re.sub(r'(\d)\s+(\d)', r'\1\2', inner)
        inner = re.sub(r'(\d)\s+(\.)', r'\1\2', inner)
        inner = re.sub(r'(\.)\s+(\d)', r'\1\2', inner)
        return inner
    text = re.sub(r'\$([^$]+?)\$', _fix_math, text)
    text = text.replace('$%', '%')
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    text = text.replace('$', '')
    text = text.replace('%', '')
    return text

def clean_images(text):
    text = re.sub(r'^\s*!\[.*?\]\(.*?\)\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    return text

def _table_to_text(table_html):
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
    if not rows:
        return ''
    header_cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[0], re.DOTALL | re.IGNORECASE)
    headers = [re.sub(r'<[^>]+>', '', c).strip() for c in header_cells]
    lines = []
    for row in rows[1:]:
        cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.DOTALL | re.IGNORECASE)
        values = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(headers) == len(values):
            parts = []
            for h, v in zip(headers, values):
                if v:
                    parts.append(h + v)
            if parts:
                lines.append('；'.join(parts))
        elif len(values) == 2:
            lines.append(values[0] + values[1])
        elif len(values) == 1:
            lines.append(values[0])
    return '；'.join(lines)

def clean_html_tables(text):
    def _replace_table(m):
        desc = _table_to_text(m.group(0))
        if desc:
            return '\n' + desc + '\n'
        return ''
    text = re.sub(r'<table[^>]*>.*?</table>', _replace_table, text, flags=re.DOTALL | re.IGNORECASE)
    return text

def clean_garbled(text):
    result = []
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF) or (0x3000 <= cp <= 0x303F) or (0xFF00 <= cp <= 0xFFEF) or (0x0020 <= cp <= 0x007E) or cp in (0x000A, 0x000D, 0x0009) or (0xFE30 <= cp <= 0xFE4F):
            result.append(c)
        elif cp == 0xFFFD:
            continue
    return ''.join(result)

def clean_footer(text):
    """检测并截断券商免责声明等页脚内容"""
    FOOTER_PATTERNS = [
        # === 免责声明/版权类 ===
        '不构成投资建议',
        '版权属于',
        '未经书面授权',
        '证券监督管理委员会',
        '本报告的版权',
        '不对因客户使用',
        '投资咨询业务资格',
        '证券公司版权所有',
        # === 适当性管理类 ===
        '证券期货投资者适当性管理办法',
        # === 分析师声明类 ===
        '本报告署名分析师在此声明',
        '撰写此报告的分析师',
        '作者保证报告所采用的数据均来自合规渠道',
        '负责本研究报告全部或部分内容的每一位证券分析师',
        '本人承诺以勤勉的执业态度',
        '分析师声明',
        '本人具有',
        '中国证券业协会',
        # === 评级体系模板类 ===
        '行业评级体系',
        '公司评级体系',
        '强烈推荐：未来',
        '市场基准指数为沪深300',
        '预计该行业指数表现',
    ]
    lines = text.split(chr(10))
    for i, line in enumerate(lines):
        for pattern in FOOTER_PATTERNS:
            if pattern in line:
                # 从匹配行所在段落的开头截断
                cut = i
                while cut > 0 and lines[cut - 1].strip():
                    cut -= 1
                result = chr(10).join(lines[:cut]).strip()
                # 如果剩余内容过少（<5%）则返回空字符串，让调用方丢弃该块
                if len(result) < len(text) * 0.05:
                    return ''
                return result
    return text


def clean_all(text):
    text = clean_latex(text)
    text = clean_images(text)
    text = clean_html_tables(text)
    text = clean_garbled(text)
    text = clean_footer(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
