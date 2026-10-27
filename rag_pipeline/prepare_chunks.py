# -*- coding: utf-8 -*-
"""
数据准备脚本（方式B Step 1）

输入：研报总MD/ 文件夹
输出：chunks.json（清洗 + 分块 + 元数据 + token_count）

用法：
  conda run -n pytorch_cpu python rag_pipeline/prepare_chunks.py
"""
import os
import json
import sys

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import MD_DIR, CHUNKS_JSON_PATH, MIN_CHUNK_TOKENS
from semantic_chunker import process_file


def main():
    print('=' * 60)
    print('RAG 数据准备: 研报清洗 + 分块')
    print('=' * 60)
    
    if not os.path.exists(MD_DIR):
        print(f'[ERROR] MD 目录不存在: {MD_DIR}')
        sys.exit(1)
    
    md_files = sorted([f for f in os.listdir(MD_DIR) if f.endswith('.md')])
    print(f'找到 {len(md_files)} 个 MD 文件')
    
    all_chunks = []
    error_files = []
    total_tokens = 0
    
    for idx, filename in enumerate(md_files):
        filepath = os.path.join(MD_DIR, filename)
        try:
            chunks = process_file(filepath)
            # 过滤短块
            before = len(chunks)
            chunks = [c for c in chunks if c['token_count'] >= MIN_CHUNK_TOKENS]
            after = len(chunks)
            
            for c in chunks:
                c['chunk_index'] = len(all_chunks)
                all_chunks.append(c)
                total_tokens += c['token_count']
            
            if (idx + 1) % 50 == 0 or idx == len(md_files) - 1:
                print(f'  [{idx+1}/{len(md_files)}] {filename[:40]:40s} {before:2d} chunks (filtered: {before-after})')
        except Exception as e:
            error_files.append((filename, str(e)))
            print(f'  [ERROR] {filename}: {e}')
    
    print(f'{"=" * 60}')
    print(f'处理完成!')
    print(f'  文件总数: {len(md_files)}')
    print(f'  成功: {len(md_files) - len(error_files)}')
    print(f'  失败: {len(error_files)}')
    print(f'  生成 chunk 总数: {len(all_chunks)}')
    print(f'  总 token 数: {total_tokens}')
    if all_chunks:
        print(f'  平均 token/chunk: {total_tokens // len(all_chunks)}')
    
    if error_files:
        print(f'错误文件列表:')
        for fn, err in error_files[:10]:
            print(f'  - {fn}: {err}')
    
    # 写入 JSON
    output = {
        'meta': {
            'total_files': len(md_files),
            'success_files': len(md_files) - len(error_files),
            'total_chunks': len(all_chunks),
            'total_tokens': total_tokens,
            'min_tokens': MIN_CHUNK_TOKENS,
        },
        'chunks': all_chunks,
    }
    
    with open(CHUNKS_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f'已保存: {CHUNKS_JSON_PATH}')
    print(f'=== 质量检查提示 ===')
    print(f'1. 检查 token_count 分布: 看上面平均 token 数')
    print(f'2. 检查公司名识别: 运行以下命令查看公司分布')
    print(f'   python -c "import json; d=json.load(open(\"{CHUNKS_JSON_PATH}\", encoding=\"utf-8\")); from collections import Counter; c=Counter(c[\"company\"] for c in d[\"chunks\"]); print(\"公司分布:\", len(c), \"家\"); [print(\"  {{k}}: {{v}}\") for k,v in c.most_common(20)]"')
    print(f'3. 检查 section 字段: 看分类是否合理')


if __name__ == '__main__':
    main()
