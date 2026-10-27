# -*- coding: utf-8 -*-
"""
语义分块器

流程：
1. 从文件头 # 标题提取公司名 + 股票代码
2. 从文件名提取年份 + 报告类型
3. 按 # 一级标题切分（第一个是文档标题，后续都是章节边界）
4. 每块打元数据
"""
import re
import os
from config import COMPANY_NAMES, COMPANY_ALIAS, REPORT_TYPE_PATTERNS, MIN_TOKENS, MAX_TOKENS


def estimate_tokens(text):
    """粗略估算 token 数"""
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[a-zA-Z0-9]', text))
    return int(chinese / 1.5 + english / 3.5 + len(text.split(chr(10))) * 0.5)


def extract_company_from_header(text, filename):
    """三层后备提取公司名"""
    header_match = re.search(r'^#\s+(.+)', text, re.MULTILINE)
    if header_match:
        header_text = header_match.group(1).strip()
        paren_match = re.search(r'^(.+?)\s*\(([0-9A-Za-z.]+)\)', header_text)
        if paren_match:
            company_candidate = paren_match.group(1).strip()
            stock_code = re.search(r'\(([0-9A-Za-z.]+)\)', header_text).group(1)
            for name in COMPANY_NAMES:
                if name in company_candidate:
                    return name, stock_code
            for alias, real in COMPANY_ALIAS.items():
                if alias in company_candidate:
                    return real, stock_code
        colon_match = re.split(r'[\uff1a:]', header_text)[0].strip()
        for name in COMPANY_NAMES:
            if name in colon_match or colon_match in name:
                return name, None
        for alias, real in COMPANY_ALIAS.items():
            if alias in colon_match:
                return real, None
    basename = os.path.splitext(os.path.basename(filename))[0]
    for name in COMPANY_NAMES:
        if name in basename:
            return name, None
    for alias, real in COMPANY_ALIAS.items():
        if alias in basename:
            return real, None
    return '\u884c\u4e1a\u7efc\u8ff0', None


def extract_metadata_from_filename(filename):
    """从文件名提取年份和报告类型"""
    basename = os.path.splitext(os.path.basename(filename))[0]
    year = None
    year_match = re.search(r'(20\d{2})', basename)
    if year_match:
        year = int(year_match.group(1))
    report_type = '\u516c\u53f8\u70b9\u8bc4'
    for pattern, rtype in REPORT_TYPE_PATTERNS:
        if re.search(pattern, basename):
            report_type = rtype
            break
    period = None
    if re.search(r'\u5e74\u62a5|FY|\u5168\u5e74', basename):
        period = 'FY'
    elif re.search(r'\u4e00\u5b63|Q1', basename):
        period = 'Q1'
    elif re.search(r'\u534a\u5e74|H1|HY', basename):
        period = 'H1'
    elif re.search(r'\u4e09\u5b63|Q3', basename):
        period = 'Q3'
    return year, report_type, period


def classify_section(heading):
    """分类章节"""
    if any(k in heading for k in ['\u6295\u8d44\u8981\u70b9', '\u6838\u5fc3\u89c2\u70b9', '\u4e1a\u7ee9']):
        return '\u4e1a\u7ee9\u5206\u6790'
    elif any(k in heading for k in ['\u8d22\u52a1', '\u76c8\u5229', '\u6bdb\u5229', '\u51c0\u5229']):
        return '\u8d22\u52a1\u5206\u6790'
    elif any(k in heading for k in ['\u98ce\u9669', '\u63d0\u793a']):
        return '\u98ce\u9669\u63d0\u793a'
    elif any(k in heading for k in ['\u884c\u4e1a', '\u5e02\u573a', '\u7ade\u4e89']):
        return '\u884c\u4e1a\u5206\u6790'
    elif any(k in heading for k in ['\u7814\u53d1', '\u521b\u65b0', '\u7ba1\u7ebf']):
        return '\u7814\u53d1\u521b\u65b0'
    elif any(k in heading for k in ['\u8bc4\u7ea7', '\u4f30\u503c']):
        return '\u8bc4\u7ea7\u4e0e\u4f30\u503c'
    elif any(k in heading for k in ['\u76ee\u5f55', 'CONTENTS']):
        return '\u76ee\u5f55'
    else:
        return '\u5176\u4ed6'


def chunk_document(text, metadata):
    """
    按 # 一级标题切分文档
    第一个 # 是文档标题，后续所有 # 都是章节边界
    """
    lines = text.split(chr(10))
    
    # 找所有 # 标题行
    heading_positions = []  # [(heading_text, line_index, is_document_title), ...]
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('# ') or stripped.startswith('## '):
            if not heading_positions:
                # 第一个 # 标题 → 文档标题
                heading_positions.append((stripped[2:].strip() if stripped.startswith('# ') else stripped[3:].strip(), i, True))
            else:
                heading_positions.append((stripped[2:].strip() if stripped.startswith('# ') else stripped[3:].strip(), i, False))
    
    if len(heading_positions) <= 1:
        # 没有章节标题，整篇作为一个 chunk
        body = text.strip()
        tokens = estimate_tokens(body)
        if tokens >= MIN_TOKENS:
            return [{
                'text': body, 'company': metadata.get('company'),
                'stock_code': metadata.get('stock_code'), 'year': metadata.get('year'),
                'period': metadata.get('period'), 'report_type': metadata.get('report_type'),
                'section': '\u5168文', 'source_file': metadata.get('source_file'),
                'token_count': tokens,
            }]
        return []
    
    chunks = []
    for idx, (heading, start_line, is_title) in enumerate(heading_positions):
        # 确定章节正文
        if idx + 1 < len(heading_positions):
            body_start = start_line + 1
            body_end = heading_positions[idx + 1][1]
        else:
            body_start = start_line + 1
            body_end = len(lines)
        
        body_lines = []
        for i in range(body_start, body_end):
            line = lines[i].strip()
            if line and not line.startswith('###'):
                body_lines.append(lines[i].rstrip())
        body = chr(10).join(body_lines).strip()
        if not body:
            continue
        
        section = classify_section(heading)
        if section == '\u76ee\u5f55':
            continue
        
        # 如果正文太短，和下一段合并
        tokens = estimate_tokens(body)
        if tokens < MIN_TOKENS and idx + 1 < len(heading_positions):
            continue  # 跳过，会合入下一段
        
        if tokens <= MAX_TOKENS:
            chunks.append({
                'text': body, 'company': metadata.get('company'),
                'stock_code': metadata.get('stock_code'), 'year': metadata.get('year'),
                'period': metadata.get('period'), 'report_type': metadata.get('report_type'),
                'section': section, 'source_file': metadata.get('source_file'),
                'token_count': tokens,
            })
        else:
            # 超过 512 token，按段落再切
            paragraphs = re.split(r'\n\s*\n', body)
            sub_text, sub_tokens = '', 0
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                pt = estimate_tokens(para)
                if sub_tokens + pt <= MAX_TOKENS:
                    sub_text = (sub_text + chr(10)*2 + para) if sub_text else para
                    sub_tokens += pt
                else:
                    if sub_tokens >= MIN_TOKENS:
                        chunks.append({
                            'text': sub_text, 'company': metadata.get('company'),
                            'stock_code': metadata.get('stock_code'), 'year': metadata.get('year'),
                            'period': metadata.get('period'), 'report_type': metadata.get('report_type'),
                            'section': section, 'source_file': metadata.get('source_file'),
                            'token_count': sub_tokens,
                        })
                    sub_text, sub_tokens = para, pt
            if sub_text and sub_tokens >= MIN_TOKENS:
                chunks.append({
                    'text': sub_text, 'company': metadata.get('company'),
                    'stock_code': metadata.get('stock_code'), 'year': metadata.get('year'),
                    'period': metadata.get('period'), 'report_type': metadata.get('report_type'),
                    'section': section, 'source_file': metadata.get('source_file'),
                    'token_count': sub_tokens,
                })
    
    return chunks


def process_file(filepath):
    """处理单个 MD 文件"""
    from document_cleaner import clean_all
    
    with open(filepath, 'r', encoding='utf-8', errors='surrogateescape') as f:
        raw_text = f.read()
    text = clean_all(raw_text)
    if not text:
        return []
    
    filename = os.path.basename(filepath)
    company, stock_code = extract_company_from_header(text, filename)
    year, report_type, period = extract_metadata_from_filename(filename)
    
    metadata = {
        'company': company, 'stock_code': stock_code,
        'year': year, 'period': period,
        'report_type': report_type, 'source_file': filename,
    }
    
    return chunk_document(text, metadata)
