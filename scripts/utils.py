"""
文字清理工具模組
用於 RAG 索引前的文本前處理
"""
import re


def clean_text(text: str) -> str:
    """
    清理從 PDF/DOCX 擷取的原始文字：
    - 移除多餘空白
    - 移除無意義特殊符號
    - 保留中文、ASCII 可列印字元、常用標點
    """
    # 統一換行
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # 移除多餘空白行（超過2行）
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 合併同行多空白
    text = re.sub(r'[ \t]+', ' ', text)
    # 移除控制字元（保留 \n）
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    return text.strip()


def infer_topic_from_filename(filename: str) -> str:
    """
    從檔名推斷衛教主題
    例：ONC-17化學藥物治療引起的副作用-口腔黏膜炎.pdf → 口腔黏膜炎
    """
    # 去副檔名
    name = re.sub(r'\.[^.]+$', '', filename)
    # 去前置編號（如 ONC-17、A01 等）
    name = re.sub(r'^[A-Za-z]{0,5}-?\d+[-_]?', '', name)
    # 取最後一個 - 之後的部分（通常是主題）
    parts = name.split('-')
    return parts[-1].strip() if parts else name.strip()
