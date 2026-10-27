# -*- coding: utf-8 -*-
"""pdf_segmenter.py: M1 PDF分块打标签
将PDF每页拆成语义块，每块标注类型标签。
不再做整页评分、不再搞固定范围搜索。

块类型标签:
  cover        - 封面/扉页
  toc          - 目录
  notice       - 重要提示/声明
  metadata     - 元信息块（股票代码、公司名称、报告期、单位声明等）
  section_title - 章节标题（一、二、三...）
  table_header - 表格标题行（利润表、资产负债表等）
  table        - 数据表格
  text         - 正文/说明文字
  footnote     - 附注/脚注
"""
import re
import pdfplumber


# === 标签识别规则 ===

# 元信息关键词（用于识别metadata块）
METADATA_KEYWORDS = [
    (r'股票代码[：:]\s*\d{6}', 'stock_code'),
    (r'股票代码\s+\d{6}', 'stock_code'),
    (r'证券代码[：:]\s*\d{6}', 'stock_code'),
    (r'证券代码\s+\d{6}', 'stock_code'),
    (r'A股代码[：:]\s*\d{6}', 'stock_code'),
    (r'股票简称[：:]', 'stock_name'),
    (r'证券简称[：:]', 'stock_name'),
    (r'公司名称[：:]', 'company_name'),
    (r'单位[：:]\s*(?:人民币)?(?:万|千|亿)?元', 'unit_declaration'),
    (r'金额单位[：:]\s*(?:人民币)?(?:万|千|亿)?元', 'unit_declaration'),
    (r'币种[：:]', 'currency'),
    (r'报告(?:期间|期)[：:]', 'report_period'),
    (r'(?:年度|季度|半年度)报告', 'report_type'),
]

# 章节标题关键词
SECTION_TITLE_PATTERNS = [
    r'^[一二三四五六七八九十]+[、．.]',
    r'^（[一二三四五六七八九十]+）',
    r'^第[一二三四五六七八九十百]+[章节部分]',
    r'^\d+[、．.]\s*[一-鿿]',
]

# 财务报表标题
FINANCIAL_TABLE_TITLES = {
    '利润表': 'income_sheet',
    '合并利润表': 'income_sheet',
    '利润及利润分配表': 'income_sheet',
    '资产负债表': 'balance_sheet',
    '合并资产负债表': 'balance_sheet',
    '现金流量表': 'cash_flow_sheet',
    '合并现金流量表': 'cash_flow_sheet',
    '主要会计数据': 'core_performance_indicators_sheet',
    '主要财务数据': 'core_performance_indicators_sheet',
    '主要财务指标': 'core_performance_indicators_sheet',
    '核心业绩指标': 'core_performance_indicators_sheet',
    '每股收益': 'core_performance_indicators_sheet',
    '基本每股收益': 'core_performance_indicators_sheet',
}

# 事项3修正: 伪标题黑名单 — 含这些词的行不是真正的财务报表
_PSEUDO_TITLE_BLACKLIST = [
    '变动分析表',    # "利润表及现金流量表相关科目变动分析表"
    '补充资料',      # "现金流量表补充资料"
    '附注',          # "利润表附注"
    # P1-5: 分部/分季度数据表不是主表
    '分部',          # "分部报告""分行业数据"
    '分季度',        # "分季度财务数据"
    '一季度',        # 分部表中按季度列示
    '二季度',
    '三季度',
    '四季度',
]

# 封面特征
COVER_KEYWORDS = ['年度报告', '季度报告', '半年度报告', '中期报告', '年度报告摘要',
                   '一季度报告', '三季度报告', '半年报', '年报']

# 目录特征
TOC_KEYWORDS = ['目录', '目  录', 'CONTENTS']


def _get_text_lines(page):
    """从页面提取文本行，保留坐标信息"""
    lines = []
    text = page.extract_text()
    if not text:
        return lines
    for line in text.split('\n'):
        line = line.strip()
        if line:
            lines.append(line)
    return lines


def _get_tables(page):
    """从页面提取表格"""
    try:
        return page.extract_tables() or []
    except:
        return []


def _classify_line(line):
    """对单行文本分类，返回(tag, sub_tag)"""
    # 元信息
    for pattern, sub_tag in METADATA_KEYWORDS:
        if re.search(pattern, line):
            return 'metadata', sub_tag

    # 财务报表标题
    for keyword, table_type in FINANCIAL_TABLE_TITLES.items():
        if keyword in line:
            # 事项3修正: 排除伪标题（如"利润表及现金流量表相关科目变动分析表"）
            if any(bl in line for bl in _PSEUDO_TITLE_BLACKLIST):
                continue
            # P0-2: 检测合并/母公司变体
            variant = 'unknown'
            if '合并' in line:
                variant = 'consolidated'
            elif '母公司' in line or '公司本部' in line:
                variant = 'parent'
            return 'table_header', (table_type, variant)

    # 章节标题
    for pat in SECTION_TITLE_PATTERNS:
        if re.match(pat, line):
            return 'section_title', None

    # 封面特征
    for kw in COVER_KEYWORDS:
        if kw in line:
            return 'cover', None

    # 目录
    for kw in TOC_KEYWORDS:
        if kw in line:
            return 'toc', None

    return 'text', None


def segment_page(page, page_idx):
    """将单页PDF分成带标签的块

    返回: list of dict
      {
        'tag': 'metadata'|'table_header'|'table'|'text'|'cover'|'toc'|'section_title'|'notice'|'footnote',
        'sub_tag': 'stock_code'|'unit_declaration'|'income_sheet'|...,
        'content': str (文本内容),
        'page_idx': int,
        'rows': list (表格数据，仅table类型),
      }
    """
    blocks = []
    lines = _get_text_lines(page)
    tables = _get_tables(page)

    # 先标记哪些行属于表格区域（避免重复归类）
    table_line_set = set()
    for tbl in tables:
        for row in tbl:
            for cell in row:
                if cell:
                    for line in lines:
                        if str(cell).strip() in line:
                            table_line_set.add(line)

    # 逐行分类
    for line in lines:
        tag, sub_tag = _classify_line(line)

        # 如果这行属于表格数据，标记为table而非text
        if tag == 'text' and line in table_line_set:
            tag = 'table'

        blocks.append({
            'tag': tag,
            'sub_tag': sub_tag,
            'content': line,
            'page_idx': page_idx,
            'rows': None,
        })

    # 独立的表格块（pdfplumber能提取的结构化表格）
    for tbl in tables:
        if tbl and len(tbl) > 1:
            blocks.append({
                'tag': 'table',
                'sub_tag': None,  # 后续根据上下文判断是哪张表
                'content': None,
                'page_idx': page_idx,
                'rows': tbl,
            })

    return blocks


def segment_pdf(pdf_path, max_pages=None):
    """对整个PDF分块打标签

    返回: list of blocks, 每块带tag/sub_tag/content/page_idx/rows
    """
    all_blocks = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages) if max_pages is None else min(max_pages, len(pdf.pages))
            for i in range(n):
                page = pdf.pages[i]
                blocks = segment_page(page, i)
                all_blocks.extend(blocks)
    except Exception as e:
        all_blocks.append({
            'tag': 'error',
            'sub_tag': None,
            'content': str(e),
            'page_idx': -1,
            'rows': None,
        })
    return all_blocks


def find_by_tag(blocks, tag, sub_tag=None):
    """按标签查找块"""
    results = []
    for b in blocks:
        if b['tag'] == tag:
            if sub_tag is None or b['sub_tag'] == sub_tag:
                results.append(b)
    return results


def find_stock_code(blocks):
    """从标签块中提取股票代码"""
    # 优先从metadata块提取
    for b in blocks:
        if b['tag'] == 'metadata' and b['sub_tag'] == 'stock_code':
            m = re.search(r'(\d{6})', b['content'])
            if m:
                return m.group(1)

    # 从所有块中搜索（有些PDF代码不在metadata块里，或格式不同）
    for b in blocks:
        if b['content']:
            # 匹配冒号和空格两种格式
            m = re.search(r'(?:股票|证券)代码[：:]\s*(\d{6})', b['content'])
            if m:
                return m.group(1)
            m = re.search(r'(?:股票|证券)代码\s+(\d{6})', b['content'])
            if m:
                return m.group(1)

    return None


def find_unit_declaration(blocks):
    """从标签块中提取单位声明"""
    for b in blocks:
        if b['tag'] == 'metadata' and b['sub_tag'] == 'unit_declaration':
            return b['content']

    # 从所有块搜索
    for b in blocks:
        if b['content']:
            if re.search(r'单位[：:]\s*(?:人民币)?(?:万|千|亿)?元', b['content']):
                return b['content']

    return None


def find_financial_tables(blocks):
    """找到所有财务报表的table_header块，返回 {table_type: [(block, variant)]}"""
    result = {}
    for b in blocks:
        if b['tag'] == 'table_header' and b['sub_tag']:
            st = b['sub_tag']
            # P0-2: sub_tag 现在是 (table_type, variant) 元组
            if isinstance(st, tuple):
                tt, variant = st
            else:
                tt, variant = st, 'unknown'
            if tt not in result:
                result[tt] = []
            result[tt].append((b, variant))
    return result


def get_section_pages(pdf_path, max_pages=50):
    """替代scan_document_structure，用M1分块定位每张表的页码

    返回: (section_pages, variant_pages, blocks)
      section_pages: dict[str, list[int]]  与scan_document_structure格式一致
      variant_pages: dict[str, dict]  {table_type: {'consolidated': [pages], 'parent': [pages], 'unknown': [pages]}}
      blocks: list[dict]  分块结果，供后续复用（单位检测等）
    """
    blocks = segment_pdf(pdf_path, max_pages=max_pages)
    tables = find_financial_tables(blocks)

    section_pages = {}
    variant_pages = {}
    for table_type, items in tables.items():
        pages = sorted(set(b['page_idx'] for b, _ in items))
        section_pages[table_type] = pages
        # P0-2: 按 variant 分组页码
        vp = {'consolidated': [], 'parent': [], 'unknown': []}
        for b, variant in items:
            vp.setdefault(variant, []).append(b['page_idx'])
        variant_pages[table_type] = {k: sorted(set(v)) for k, v in vp.items()}

    # 确保4张表都有key
    for tt in ['income_sheet', 'balance_sheet', 'cash_flow_sheet', 'core_performance_indicators_sheet']:
        if tt not in section_pages:
            section_pages[tt] = []
        if tt not in variant_pages:
            variant_pages[tt] = {'consolidated': [], 'parent': [], 'unknown': []}

    return section_pages, variant_pages, blocks
