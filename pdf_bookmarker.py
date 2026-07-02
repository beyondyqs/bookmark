"""
PDF 自动书签生成工具 v3
- 自动在副本上操作，保护原始文件
- 用户指定目录页类型（图片/乱码/正常），决定识别方式
- 罗马数字跳过仅依据页数位置，不影响章节名称中含罗马字符的条目
- 直接写入PDF书签（Outline），无需WPS自动化
"""
import re
import json
import shutil
import base64
import difflib
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from pathlib import Path
import fitz
from openai import OpenAI


CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config():
    default = {
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_api_key": "",
        "llm_model": "deepseek-chat",
        "llm_vision_model": "",
        "vision_base_url": "",
        "vision_api_key": "",
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in default:
                    default[k] = v
                elif k == "llm_base_url":
                    default["llm_base_url"] = v
                elif k == "llm_api_key":
                    default["llm_api_key"] = v
        except Exception:
            pass
    if not default.get("vision_base_url"):
        default["vision_base_url"] = default["llm_base_url"]
    if not default.get("vision_api_key"):
        default["vision_api_key"] = default["llm_api_key"]
    return default


CONFIG = load_config()


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=4)


_TkRoot = None


def tk_root():
    global _TkRoot
    if _TkRoot is None:
        _TkRoot = tk.Tk()
        _TkRoot.withdraw()
        _TkRoot.attributes("-topmost", True)
    return _TkRoot


def select_pdf_file():
    root = tk_root()
    path = filedialog.askopenfilename(
        title="选择PDF文件",
        filetypes=[("PDF文件", "*.pdf"), ("所有文件", "*.*")]
    )
    return path


def input_page_info():
    """通过命令行收集用户输入"""
    print("\n" + "=" * 50)
    print("请输入以下信息:")
    print("=" * 50)

    while True:
        print("\n选择目录页提取方式:")
        print("  1 - 自动方式（推荐）")
        print("  2 - 用 Edge 浏览器提取")
        print("  3 - 图片/乱码，直接调大模型识别")
        choice = input("请输入数字 (1/2/3): ").strip()
        if choice == "1":
            toc_type = "normal"
            break
        elif choice == "2":
            toc_type = "edge"
            break
        elif choice == "3":
            toc_type = "image"
            break
        else:
            print("无效输入，请输入 1、2 或 3")

    def read_int(prompt):
        while True:
            try:
                v = int(input(prompt).strip())
                if v < 1:
                    print("页码必须 >= 1，请重新输入")
                    continue
                return v
            except ValueError:
                print("请输入有效整数")

    toc_start = read_int("目录开始页（PDF页码）: ")
    while True:
        toc_end = read_int("目录结束页（PDF页码）: ")
        if toc_end >= toc_start:
            break
        print("目录结束页必须 >= 目录开始页，请重新输入")

    offset = read_int("正文偏移量（正文第1页的PDF页码 - 1）: ")

    return (toc_type, toc_start, toc_end, offset)


def create_backup(original_path):
    original = Path(original_path)
    backup_name = original.parent / f"副本_{original.name}"
    try:
        shutil.copy2(original, backup_name)
    except PermissionError:
        print(f"[错误] 无法复制文件，PDF可能被其他程序（如WPS）占用")
        print(f"       请先关闭PDF文件，然后重新运行程序")
        messagebox.showerror("文件被占用",
            f"无法创建副本，PDF文件可能被其他程序打开占用了。\n\n"
            f"请先关闭PDF文件（WPS、Adobe等），然后重新运行程序。\n\n"
            f"文件路径:\n{original}")
        return None
    except Exception as e:
        print(f"[错误] 复制文件失败: {e}")
        messagebox.showerror("复制失败", f"无法创建副本文件:\n{e}")
        return None
    print(f"[备份] 已创建副本: {backup_name}")
    return str(backup_name)


def extract_toc_text(pdf_path, toc_start, toc_end):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[错误] 无法打开PDF文件: {e}")
        return ""
    lines = []
    for page_num in range(toc_start - 1, toc_end):
        page = doc[page_num]

        text_a = page.get_text("text").strip()
        text_b_lines = []
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                t = "".join(span["text"] for span in line["spans"]).strip()
                if t:
                    text_b_lines.append(t)
        text_b = "\n".join(text_b_lines).strip()

        if len(text_a) >= len(text_b):
            page_lines = text_a.splitlines() if text_a else text_b_lines
        else:
            page_lines = text_b_lines

        if not page_lines:
            try:
                text_c = page.get_text("html").strip()
                if text_c:
                    page_lines = text_c.splitlines()
            except Exception:
                pass

        if not page_lines:
            page_lines = ["[此页无法提取文字，可能是图片]"]

        lines.append(f"--- 第 {page_num + 1} 页 ---")
        lines.extend(page_lines)

    expected = toc_end - toc_start + 1
    actual = sum(1 for l in lines if l.startswith("--- 第"))
    if actual < expected:
        print(f"  [警告] 目录应提取 {expected} 页，实际只提取到 {actual} 页文本")

    doc.close()
    return "\n".join(lines)


def extract_toc_text_edge(pdf_path, toc_start, toc_end):
    try:
        import pyperclip
    except ImportError:
        return None
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_path = None
    for p in candidates:
        if Path(p).exists():
            edge_path = p
            break
    if not edge_path:
        return None

    pdf_url = Path(pdf_path).resolve().as_uri()
    lines = []

    for page_num in range(toc_start, toc_end + 1):
        page_url = f"{pdf_url}#page={page_num}"
        try:
            subprocess.Popen([edge_path, page_url],
                             shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"  启动Edge失败: {e}")
            return None
        time.sleep(2.5)

        try:
            import pyautogui as pg
            pg.hotkey("ctrl", "a")
            time.sleep(0.5)
            pg.hotkey("ctrl", "c")
            time.sleep(0.5)
        except Exception:
            pass

        page_text = pyperclip.paste()
        lines.append(f"--- 第 {page_num} 页 ---")
        lines.append(page_text.strip())

        try:
            pg.hotkey("ctrl", "w")
            time.sleep(0.5)
        except Exception:
            pass

    return "\n".join(lines)


def extract_toc_text_fallback(pdf_path, toc_start, toc_end):
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        lines = []
        with pdfplumber.open(pdf_path) as pdf:
            for page_num in range(toc_start - 1, toc_end):
                page = pdf.pages[page_num]
                text = page.extract_text() or ""
                lines.append(f"--- 第 {page_num + 1} 页 ---\n{text}")
        return "\n".join(lines)
    except Exception as e:
        print(f"  pdfplumber提取失败: {e}")
        return None


def render_toc_pages(pdf_path, toc_start, toc_end):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[错误] 无法打开PDF文件: {e}")
        return None
    images = []
    TARGET_LONG_EDGE = 1500
    for page_num in range(toc_start - 1, toc_end):
        page = doc[page_num]
        rect = page.rect
        max_dim = max(rect.width, rect.height)
        zoom = TARGET_LONG_EDGE / max_dim if max_dim > 0 else 1.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        buf = pix.tobytes("jpeg")
        b64 = base64.b64encode(buf).decode("utf-8")
        images.append({"page_num": page_num + 1, "base64": b64})
    doc.close()
    return images


ROMAN_CHARS_RE = re.compile(r'^[IVXLCDMivxlcdm]{1,10}$')

ROMAN_VALUES = {
    'I': 1, 'V': 5, 'X': 10, 'L': 50,
    'C': 100, 'D': 500, 'M': 1000,
    'i': 1, 'v': 5, 'x': 10, 'l': 50,
    'c': 100, 'd': 500, 'm': 1000,
}


def is_valid_roman(s):
    s = s.strip()
    if not s or not ROMAN_CHARS_RE.match(s):
        return False
    vals = [ROMAN_VALUES[c] for c in s]
    total = 0
    i = 0
    while i < len(vals):
        if i + 1 < len(vals) and vals[i] < vals[i + 1]:
            total += vals[i + 1] - vals[i]
            i += 2
        else:
            total += vals[i]
            i += 1
    return total > 0


def detect_level(title):
    cleaned = re.sub(r'^[§#]\s*', '', title)
    if re.match(r'^第\s*[一二三四五六七八九十百千\d]+\s*[编篇章节部分]', cleaned):
        return 1
    m = re.match(r'(\d+(?:\.\d+)*\.?)', cleaned)
    if m:
        raw = m.group(1)
        if raw.endswith('.'):
            parts = raw[:-1].split('.')
            depth = len(parts) + 1
        else:
            depth = len(raw.split('.'))
        return min(depth, 5)
    return 1


SEP_CHARS = r'.．…·‥⸱•\s\-–—'

def normalize_line(line):
    line = re.sub(r'[\u00A0\u00AD\u200B-\u200F\u2028-\u202F\uFEFF]+', ' ', line)
    line = re.sub(r'\s+', ' ', line)
    return line.strip()


def is_chapter_fragment(line):
    line = line.strip()
    if not line:
        return False
    if len(line) > 60:
        return False
    sep_class = f'[{SEP_CHARS}]'
    if re.search(sep_class + r'{2,}\d+\s*$', line):
        return False
    if re.search(sep_class + r'{2,}[IVXLCDMivxlcdm]{2,}\s*$', line):
        return False
    if re.match(r'^第\s*[一二三四五六七八九十百千\d]+\s*[编篇章节部分]', line):
        return True
    if re.match(r'^[§#]\s*\d+', line):
        return True
    if re.match(r'^(Chapter|Section|Part|Unit|Lesson|Module|Topic)\s+\w+', line, re.IGNORECASE):
        return True
    if re.match(r'^[IVXLCDMivxlcdm]{1,5}\s*$', line):
        return True
    if re.match(r'^\d{1,2}\s*$', line):
        return True
    words = line.split()
    if len(words) <= 2 and re.search(r'\d+', line):
        return True
    return False


def parse_toc_locally(raw_text):
    entries = []
    seen_titles = set()
    sep_class = f'[{SEP_CHARS}]'

    raw_lines = raw_text.splitlines()
    merged_lines = []
    i = 0
    while i < len(raw_lines):
        line = normalize_line(raw_lines[i])
        if not line or line.startswith('---'):
            merged_lines.append(raw_lines[i])
            i += 1
            continue

        if is_chapter_fragment(line):
            fragments = [raw_lines[i]]
            j = i + 1
            title_line = None
            title_idx = -1

            while j < len(raw_lines):
                nxt = normalize_line(raw_lines[j])
                if not nxt:
                    j += 1
                    continue
                if nxt.startswith('---'):
                    break
                if is_chapter_fragment(nxt):
                    fragments.append(raw_lines[j])
                    j += 1
                    continue
                nxt_has_page = re.search(r'\d+\s*$', nxt) and (
                    re.search(r'(?:' + sep_class + r'+)\d+\s*$', nxt) or re.search(r'\s+\d+\s*$', nxt))
                nxt_has_roman = re.search(r'(?:' + sep_class + r'+)([IVXLCDMivxlcdm]+)\s*$', nxt)
                if nxt_has_page or (nxt_has_roman and is_valid_roman(nxt_has_roman.group(1))):
                    title_line = raw_lines[j]
                    title_idx = j
                    break
                fragments.append(raw_lines[j])
                j += 1
                continue

            if title_line is not None:
                merged = ' '.join(fragments) + ' ' + title_line
                merged_lines.append(merged)
                i = title_idx + 1
                continue

            for k in range(i + 1, min(i + 4, len(raw_lines))):
                fb = normalize_line(raw_lines[k])
                if fb and not fb.startswith('---'):
                    fb_has_page = re.search(r'\d+\s*$', fb) and (
                        re.search(r'(?:' + sep_class + r'+)\d+\s*$', fb) or re.search(r'\s+\d+\s*$', fb))
                    if fb_has_page:
                        merged = ' '.join(fragments) + ' ' + raw_lines[k]
                        merged_lines.append(merged)
                        i = k + 1
                        break
            else:
                for f in fragments:
                    merged_lines.append(f)
                i = j if j < len(raw_lines) else len(raw_lines)
            continue

        merged_lines.append(raw_lines[i])
        i += 1

    for raw_line in merged_lines:
        line = normalize_line(raw_line)
        if not line or len(line) < 3:
            continue
        if line.startswith('---'):
            continue

        has_sep = bool(re.search(sep_class + r'{2,}', line))

        page_match = re.search(r'(\d+)\s*$', line)
        if page_match:
            page_num = int(page_match.group(1))
            if not (1 <= page_num <= 9999):
                continue
        else:
            roman_match = re.search(r'(?:' + sep_class + r'+)([IVXLCDMivxlcdm]+)\s*$', line)
            if roman_match:
                if is_valid_roman(roman_match.group(1)):
                    print(f"  [跳过罗马数字页码] {line.strip()}")
                    continue
            continue

        if not has_sep and re.match(r'^[\d]+$', line[:page_match.start()].strip()):
            continue

        title_end = page_match.start()
        title_candidate = line[:title_end].rstrip(SEP_CHARS + ' ')
        title_candidate = re.sub(r'\s+', ' ', title_candidate).strip()

        title_clean = re.sub(r'^[§#]\s*', '', title_candidate).strip()
        if not title_clean or title_clean in seen_titles:
            continue

        if re.match(r'^[\d' + sep_class + r']+$', title_clean):
            continue

        level = detect_level(title_candidate)
        entries.append({"title": title_candidate, "page": page_num, "level": level})
        seen_titles.add(title_clean)

    if len(entries) >= 2:
        print(f"  本地解析成功，识别到 {len(entries)} 个条目")
        return entries
    return None


PER_PAGE_PROMPT = """你是PDF目录解析专家。下面是PDF目录页第{page_num}页（共{total_pages}页）的文本。

# 内容提取规则
1. 完整提取所有目录项，一个不漏。有页码的条目和篇/章等无页码大标题都要提取。
2. 忠于原文，严格保留原始标题的文字和序号前缀，禁止增删修改。
3. 标题和页码分行时合并为一条，禁止将序号和标题拆分为独立条目。
4. 忽略页眉页脚等非目录内容，但Introduction、Appendix、Index、Bibliography、References、索引、参考文献、附录等也应提取。
5. 页数是罗马数字（I,II,III,IV,V,vi,vii等）则page设为null，否则必须是阿拉伯数字。
6. 无页码的篇/章级大标题，根据下级首条页码或相邻条目推算填补，禁止填null。
7. 仅根据页数判断是否跳过，不要因章节名称中的罗马字符而跳过。

# 层级判定规则
1. 篇/部分 > 章 > 节 > 子节，层级依次递增（1,2,3,4...）。
2. 如第1章=1级，1.1=2级，1.1.1=3级，1.1.1.1=4级。
3. 前言、致谢、参考文献、索引等通常为第1层级。
4. 除第一页外，本页首行标题可能隶属于上一页，请根据上下文推断层级。

# 输出格式
JSON数组：[{{"title":"完整序号+名称","page":页码或null,"level":层级}}]
只输出JSON数组，不要包含其他内容。

本页文本：
{page_text}"""


def _parse_llm_response(content):
    content = re.sub(r'^```(?:json)?\s*\n?', '', content)
    content = re.sub(r'\n?\s*```$', '', content)
    entries = json.loads(content)
    for e in entries:
        if not isinstance(e.get("page"), (int, float)):
            try:
                e["page"] = int(e["page"])
            except (ValueError, TypeError):
                e["page"] = None
    return entries


def parse_toc_with_llm(raw_text):
    client = OpenAI(
        base_url=CONFIG["llm_base_url"],
        api_key=CONFIG["llm_api_key"],
    )

    page_marker = re.compile(r'^---\s*\S*\s*\d+\s*\S*\s*---')
    pages = []
    current = []
    for line in raw_text.splitlines():
        if page_marker.match(line.strip()):
            if current:
                pages.append('\n'.join(current))
                current = []
        else:
            current.append(line)
    if current:
        pages.append('\n'.join(current))

    pages = [p.strip() for p in pages]
    total = len(pages)

    if total <= 1:
        prompt = PER_PAGE_PROMPT.format(page_num=1, total_pages=1, page_text=raw_text)
        response = client.chat.completions.create(
            model=CONFIG["llm_model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return _parse_llm_response(response.choices[0].message.content)

    all_entries = []
    for idx, page_text in enumerate(pages, 1):
        if not page_text.strip():
            continue
        prompt = PER_PAGE_PROMPT.format(page_num=idx, total_pages=total, page_text=page_text)
        try:
            response = client.chat.completions.create(
                model=CONFIG["llm_model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            page_entries = _parse_llm_response(response.choices[0].message.content)
            all_entries.extend(page_entries)
        except Exception as e:
            print(f"  第{idx}页识别失败: {e}")

    seen = set()
    deduped = []
    for e in all_entries:
        key = (e.get("title", "").strip(), e.get("page"))
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped


def parse_toc_with_llm_vision(pdf_path, toc_start, toc_end):
    vision_model = CONFIG.get("llm_vision_model") or CONFIG["llm_model"]
    vision_url = CONFIG.get("vision_base_url") or CONFIG["llm_base_url"]
    vision_key = CONFIG.get("vision_api_key") or CONFIG["llm_api_key"]
    client = OpenAI(
        base_url=vision_url,
        api_key=vision_key,
    )

    pages = render_toc_pages(pdf_path, toc_start, toc_end)
    if pages is None:
        return None

    all_entries = []
    total = len(pages)

    for idx, pg in enumerate(pages, 1):
        prompt_text = (
            "这是PDF目录页的第%d页（共%d页），请识别本页中的所有章节条目。\n"
            "# 内容提取规则\n"
            "1. 完整提取所有目录项，一个不漏。有页码的条目和篇/章等无页码大标题都要提取。\n"
            "2. 忠于原文，严格保留原始标题的文字和序号前缀，禁止增删修改。\n"
            "3. 标题和页码分行时合并为一条，禁止将序号和标题拆分为独立条目。\n"
            "4. 忽略页眉页脚、书籍名称等非目录内容。\n"
            "5. 页数是罗马数字（I,II,III,IV,V,vi,vii等）则page设为null，否则必须是阿拉伯数字。\n"
            "6. 无页码的篇/章级大标题，根据下级首条页码或相邻条目推算填补，禁止填null。\n"
            "7. 仅根据页数判断是否跳过，不要因章节名称中的罗马字符而跳过。\n"
            "8. 章节编号保留：图上为\"01 花草篇\"则提取\"01 花草篇\"，禁止改为\"花草篇\"。\n"
            "9. 符号处理：带圈数字替换为常规(①→1)；去除标题页码间引导点(·)；\n"
            "   标题标点：中文用全角(：，)、英文用半角(: ,)。\n"
            "# 层级判定规则\n"
            "1. 视觉特征优先：根据字号、缩进、颜色等视觉特征判定层级，结合语义推断。\n"
            "2. 篇/部分 > 章 > 节 > 子节，层级依次递增（1,2,3,4...）。\n"
            "3. 2.4节与2.4.1子节绝不可同级（2.4=2级,2.4.1=3级）。\n"
            "4. 前言、致谢、参考文献、索引等通常为第1层级。\n"
            "5. 除第一页外，本页首行标题可能隶属于上一页，请根据上下文推断层级。\n"
            "6. 避免误区：字号/字体/缩进/颜色不同的标题，层级一定不同。\n"
            "7. 页面上出现\"xx篇\"\"xx章\"尽管无页码，也应提取为目录项。\n"
            "# 输出格式\n"
            "JSON数组：[{\"title\":\"完整序号+名称\",\"page\":页码或null,\"level\":层级}]\n"
            "只输出JSON数组，不要包含其他内容。"
        ) % (idx, total)

        formats = [
            [{"type": "text", "text": prompt_text},
             {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{pg['base64']}"}}],
            [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{pg['base64']}"}},
             {"type": "text", "text": prompt_text}],
        ]

        content = None
        for fmt in formats:
            try:
                response = client.chat.completions.create(
                    model=vision_model,
                    messages=[{"role": "user", "content": fmt}],
                    temperature=0.1,
                    extra_body={"enable_thinking": False},
                )
                content = response.choices[0].message.content.strip()
                content = re.sub(r'^```(?:json)?\s*\n?', '', content)
                content = re.sub(r'\n?\s*```$', '', content)
                page_entries = json.loads(content)
                for e in page_entries:
                    if not isinstance(e.get("page"), (int, float)):
                        try:
                            e["page"] = int(e["page"])
                        except (ValueError, TypeError):
                            e["page"] = None
                all_entries.extend(page_entries)
                break
            except json.JSONDecodeError:
                continue
            except Exception as e:
                err = str(e)
                if 'InvalidParameter' in err or 'not both' in err:
                    continue
                print(f"  第{idx}页视觉识别失败: {e}")
                break

        if content is None:
            print(f"  第{idx}页视觉识别失败（API不支持图片格式或返回非JSON）")

    if not all_entries:
        return None
    return all_entries


def verify_last_chapter_page(pdf_path, entries, offset):
    if not entries:
        return True
    last = entries[-1]
    title = last["title"]
    rel_page = last["page"]
    if not isinstance(rel_page, (int, float)):
        try:
            rel_page = int(rel_page)
        except (ValueError, TypeError):
            print(f"  [验证跳过] 最后一个章节页数不是有效数字: {rel_page}")
            return True
    actual_page = offset + rel_page
    if actual_page < 1:
        actual_page = 1

    try:
        doc = fitz.open(pdf_path)
        page = doc[actual_page - 1]
        page_text = page.get_text("text")
        doc.close()
    except Exception as e:
        print(f"  [验证] 无法读取第 {actual_page} 页: {e}")
        return True

    title_clean = re.sub(r'^[§#]\s*', '', title).strip()
    title_keywords = re.split(r'[\s,，.．、]+', title_clean)
    title_keywords = [w for w in title_keywords if len(w) >= 2]

    if not title_keywords:
        return True

    found = sum(1 for kw in title_keywords if kw in page_text)
    ratio = found / len(title_keywords)

    if ratio < 0.3:
        print(f"\n  [验证警告] 最后一个章节 \"{title}\"")
        print(f"     目录中相对页数: {rel_page}，计算实际页: {offset} + {rel_page} = {actual_page}")
        print(f"     第 {actual_page} 页内容未匹配到章节标题（匹配度 {ratio:.0%}）")
        print(f"     PDF可能存在页面缺失或多余的情况")
        return False

    print(f"  [验证通过] \"{title}\" 确认位于第 {actual_page} 页")
    return True


def add_bookmarks_to_pdf(pdf_path, entries, offset, toc_bookmark_page=None):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[错误] 无法打开PDF写入书签: {e}")
        return 0
    toc = doc.get_toc()

    new_items = []
    for e in entries:
        if e.get("page") is None:
            continue
        rel_page = e["page"]
        if not isinstance(rel_page, (int, float)):
            try:
                rel_page = int(rel_page)
            except (ValueError, TypeError):
                continue
        if rel_page < 1:
            continue
        title = e["title"]
        level = e.get("level", 1)
        if not isinstance(level, (int, float)):
            try:
                level = int(level)
            except (ValueError, TypeError):
                level = 1
        actual_page = offset + rel_page
        actual_page = max(actual_page, 1)
        new_items.append([level, title, actual_page])

    if toc_bookmark_page is not None:
        new_items.insert(0, [1, "目录", toc_bookmark_page])

    dedup = {}
    for item in new_items:
        key = (item[2], item[1].strip())
        existing = dedup.get(key)
        if existing is None:
            dedup[key] = item
            continue
        if len(item[1]) > len(existing[1]):
            dedup[key] = item

    toc_dict = {}
    for t in toc:
        toc_dict[t[1]] = t
    for item in dedup.values():
        if item is not None:
            toc_dict[item[1]] = item
    toc = sorted(toc_dict.values(), key=lambda x: (x[2], x[0]))

    if not toc:
        return 0

    if toc[0][0] != 1:
        toc[0] = [1] + toc[0][1:]

    for i in range(1, len(toc)):
        if toc[i-1][1] == "目录" and toc[i][0] > 1:
            toc[i][0] = 1

    total_pages = doc.page_count
    for item in toc:
        if item[2] < 1 or item[2] > total_pages:
            item[2] = max(1, min(item[2], total_pages))

    for i in range(len(toc)):
        if toc[i][0] < 1:
            toc[i][0] = 1
        if i > 0 and toc[i][0] > toc[i-1][0] + 1:
            toc[i][0] = toc[i-1][0] + 1

    doc.set_toc(toc)

    import os, time

    tmp = pdf_path + ".tmp"
    try:
        doc.save(tmp, garbage=4, deflate=True)
    except Exception as e:
        doc.close()
        raise RuntimeError(f"保存PDF失败: {e}") from e
    doc.close()

    for attempt in range(5):
        try:
            os.replace(tmp, pdf_path)
            break
        except PermissionError:
            if attempt < 4:
                time.sleep(1)
                continue
            try:
                shutil.copy2(tmp, pdf_path)
                os.remove(tmp)
            except Exception as e2:
                raise RuntimeError(
                    f"无法写入PDF文件，文件可能被WPS等程序占用。请关闭PDF后重试。\n{e2}") from e2

    return len([v for v in dedup.values() if v is not None])


def ask_api_key():
    root = tk_root()
    key = simpledialog.askstring("API Key 设置", "请输入 LLM API Key（将保存到config.json）:", parent=root, show="*")
    if key:
        CONFIG["llm_api_key"] = key
        save_config()
        return True
    return False


def edit_config_gui():
    root = tk_root()
    new_url = simpledialog.askstring("配置", "文本模型 API 地址:", parent=root, initialvalue=CONFIG["llm_base_url"])
    if not new_url:
        return False
    new_key = simpledialog.askstring("配置", "文本模型 API Key（留空保持当前值）:", parent=root, initialvalue="", show="*")
    if new_key is None:
        return False
    new_model = simpledialog.askstring("配置", "文本模型名称:", parent=root, initialvalue=CONFIG["llm_model"])
    if not new_model:
        return False
    new_vision_url = simpledialog.askstring("配置", "视觉模型 API 地址（留空同文本模型）:", parent=root, initialvalue=CONFIG.get("vision_base_url", ""))
    if new_vision_url is None:
        return False
    new_vision_key = simpledialog.askstring("配置", "视觉模型 API Key（留空同文本模型）:", parent=root, initialvalue="", show="*")
    if new_vision_key is None:
        return False
    new_vision = simpledialog.askstring("配置", "视觉模型名称（如gpt-4o，留空无法使用图片识别）:", parent=root, initialvalue=CONFIG.get("llm_vision_model", ""))
    if new_vision is None:
        return False
    CONFIG["llm_base_url"] = new_url
    if new_key:
        CONFIG["llm_api_key"] = new_key
    CONFIG["llm_model"] = new_model
    if new_vision_url:
        CONFIG["vision_base_url"] = new_vision_url
    if new_vision_key:
        CONFIG["vision_api_key"] = new_vision_key
    CONFIG["llm_vision_model"] = new_vision
    save_config()
    return True


def show_menu():
    root = tk_root()
    answer = messagebox.askyesnocancel("PDF自动书签工具", "是否开始处理PDF？\n\n【是】开始处理\n【否】配置API\n【取消】退出", parent=root)
    if answer is None:
        return "exit"
    elif answer:
        return "start"
    else:
        return "config"


def clear_bookmarks(pdf_path):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"  [跳过] 无法打开PDF清除旧书签: {e}")
        return
    try:
        doc.set_toc([])
        tmp = pdf_path + ".tmp"
        doc.save(tmp, garbage=4, deflate=True)
        doc.close()
        import os
        os.replace(tmp, pdf_path)
        print(f"  已清除副本中全部已有书签")
    except Exception as e:
        print(f"  [注意] 无法清除旧书签，继续使用已有书签: {e}")
        try:
            doc.close()
        except Exception:
            pass


def find_and_verify_last_chapter(pdf_path, toc_end, offset):
    print(f"  提取最后一页(第{toc_end}页)识别最后一个章节...")
    last_text = extract_toc_text(pdf_path, toc_end, toc_end)
    if not last_text.strip():
        return True

    entries = parse_toc_locally(last_text)
    if not entries:
        if not CONFIG["llm_api_key"]:
            ask_api_key()
        if CONFIG["llm_api_key"]:
            try:
                entries = parse_toc_with_llm(last_text)
            except Exception:
                pass

    if not entries:
        return True

    last_patterns = ['index', 'bibliography', 'references', 'appendix',
                     '索引', '参考文献', '附录', 'notation', 'symbol', 'glossary']
    last = None
    for e in reversed(entries):
        if any(p in e.get('title', '').lower() for p in last_patterns):
            last = e
            break

    if last is None:
        last = max(entries, key=lambda e: (e.get('page', 0) or 0))

    if last is None:
        return True

    rel_page = last["page"]
    title = last["title"]
    if not isinstance(rel_page, (int, float)):
        try:
            rel_page = int(rel_page)
        except (ValueError, TypeError):
            print(f"  [验证跳过] 最后一个章节页数不是有效数字: {rel_page}")
            return True
    actual_page = offset + rel_page
    if actual_page < 1:
        return True

    try:
        doc = fitz.open(pdf_path)
        page = doc[actual_page - 1]
        page_text = page.get_text("text")
        doc.close()
    except Exception:
        return True

    keywords = [w for w in re.split(r'[\s,，.．、]+', re.sub(r'^[§#]\s*', '', title))
                if len(w) >= 2]
    if not keywords:
        return True

    found_ratio = sum(1 for kw in keywords if kw in page_text) / len(keywords)

    if found_ratio >= 0.3:
        print(f"  [验证通过] 最后一个章节 \"{title}\" 确认位于第 {actual_page} 页")
        return True

    print(f"\n  [验证警告] 最后一个章节 \"{title}\"")
    print(f"     目录中相对页数: {rel_page}，计算实际页: {offset} + {rel_page} = {actual_page}")
    print(f"     第 {actual_page} 页内容未匹配到章节标题（匹配度 {found_ratio:.0%}）")
    cont = messagebox.askyesno("页码可能不准确",
        f"最后一个章节 \"{title}\" 的\n"
        f"计算页码（第 {actual_page} 页）内容不匹配。\n\n"
        f"PDF可能存在页面缺失或多余的情况。\n"
        f"是否仍要继续？")
    return cont


def main():
    backup_path = None
    original_path = None
    success = False

    try:
        while True:
            action = show_menu()
            if action == "exit":
                print("程序退出")
                return
            elif action == "config":
                if edit_config_gui():
                    print("配置已保存")
                continue
            elif action == "start":
                break

        original_path = select_pdf_file()
        if not original_path:
            print("未选择文件，程序退出")
            return
        print(f"\n[选中文件] {original_path}")

        backup_path = create_backup(original_path)
        if backup_path is None:
            use_original = messagebox.askyesno("无法创建副本",
                "无法创建副本文件（PDF可能被其他程序占用）。\n\n"
                "是否直接在原始文件上操作？（建议先关闭其他程序后重试）")
            if not use_original:
                print("用户取消，程序退出")
                return
            backup_path = original_path
            print(f"[注意] 将在原始文件上直接操作: {backup_path}")
        else:
            print("  [1/5] 清除副本中已有书签...")
            clear_bookmarks(backup_path)

        toc_type, toc_start, toc_end, offset = input_page_info()
        if toc_type is None or toc_start is None:
            print("未输入完整信息，程序退出")
            return
        type_names = {"image": "大模型图片识别", "edge": "Edge浏览器提取", "normal": "自动方式"}
        print(f"[目录类型] {type_names.get(toc_type, toc_type)}")
        print(f"[目录范围] 第 {toc_start} 页 ~ 第 {toc_end} 页")
        print(f"[正文偏移量] {offset}")

        print("\n[1/5] 优先验证最后一个章节的页码...")
        if not find_and_verify_last_chapter(backup_path, toc_end, offset):
            print("  用户终止，程序退出")
            messagebox.showinfo("已终止", "已终止书签生成。")
            return

        use_vision = toc_type == "image"
        entries = None

        if toc_type == "edge":
            print("\n[2/5] 用Edge浏览器提取目录文字...")
            raw_text = extract_toc_text_edge(backup_path, toc_start, toc_end)
            if raw_text is None or not raw_text.strip():
                print("  Edge提取失败，回退到自动方式...")
                raw_text = extract_toc_text(backup_path, toc_start, toc_end)
            else:
                print(f"  共 {toc_end - toc_start + 1} 页，文本长度 {len(raw_text)} 字符")

            print("[3/5] 解析目录...")
            entries = parse_toc_locally(raw_text)
            if entries is None and CONFIG["llm_api_key"]:
                print("  本地解析失败，尝试调用大模型...")
                try:
                    entries = parse_toc_with_llm(raw_text)
                    print(f"  大模型解析成功，识别到 {len(entries)} 个条目")
                except Exception as e:
                    print(f"  大模型解析失败: {e}")

        elif use_vision:
            print("\n[2/5] 调用大模型识别目录页图片...")
            if not CONFIG["llm_api_key"]:
                if not ask_api_key():
                    return
            entries = parse_toc_with_llm_vision(backup_path, toc_start, toc_end)
            if entries is not None:
                print(f"  图片识别成功，识别到 {len(entries)} 个条目")
            else:
                print("  图片识别失败，尝试文本识别...")
                raw_text = extract_toc_text(backup_path, toc_start, toc_end)
                try:
                    entries = parse_toc_with_llm(raw_text)
                    print(f"  文本识别成功，识别到 {len(entries)} 个条目")
                except Exception as e:
                    print(f"  文本识别也失败: {e}")
                    messagebox.showerror("错误", f"识别失败:\n{e}")
                    return
        else:
            print("\n[2/5] 提取目录文本...")
            raw_text = extract_toc_text(backup_path, toc_start, toc_end)
            print(f"  共 {toc_end - toc_start + 1} 页，文本长度 {len(raw_text)} 字符")

            print("[3/5] 解析目录...")
            entries = parse_toc_locally(raw_text)

            if entries is None:
                print("  PyMuPDF本地解析未识别到足够条目，尝试pdfplumber...")
                raw_text2 = extract_toc_text_fallback(backup_path, toc_start, toc_end)
                if raw_text2 is not None:
                    print(f"  pdfplumber提取完成，文本长度 {len(raw_text2)} 字符")
                    entries = parse_toc_locally(raw_text2)

            if entries is None:
                print("  pdfplumber也未识别到足够条目，尝试Edge浏览器提取...")
                raw_text = extract_toc_text_edge(backup_path, toc_start, toc_end)
                if raw_text is not None:
                    print(f"  Edge提取完成，文本长度 {len(raw_text)} 字符")
                    entries = parse_toc_locally(raw_text)

            if entries is None:
                print("  文本提取均未识别到足够条目，尝试调用大模型...")
                if not CONFIG["llm_api_key"]:
                    if not ask_api_key():
                        return
                try:
                    entries = parse_toc_with_llm(raw_text)
                    print(f"  大模型解析成功，识别到 {len(entries)} 个条目")
                except Exception as e:
                    print(f"  大模型解析失败: {e}")
                    messagebox.showerror("错误", f"大模型解析失败:\n{e}")
                    return

        if not entries:
            print("未解析到任何章节，程序退出")
            messagebox.showerror("错误", "未解析到任何章节，请检查目录页范围。")
            return

        print(f"  解析结果 ({len(entries)} 个章节):")
        for e in entries:
            indent = "  " * (e.get("level", 1) - 1)
            print(f"  {indent}{e['title']} -> 第 {e['page']} 页")

        print("\n[4/5] 写入书签到PDF...")
        try:
            count = add_bookmarks_to_pdf(backup_path, entries, offset, toc_bookmark_page=toc_start)
            print(f"  成功写入 {count} 个书签")
        except Exception as e:
            print(f"  写入失败: {e}")
            messagebox.showerror("错误", f"写入书签失败:\n{e}")
            return

        print("\n[5/5] 完成")
        print(f"  副本文件: {backup_path}")
        print(f"  原始文件: {original_path}（未被修改）")
        success = True
        messagebox.showinfo("完成",
            f"书签添加完成！\n"
            f"成功写入 {count} 个书签\n\n"
            f"副本文件: 副本_{Path(original_path).name}\n"
            f"原始文件: 未修改\n\n"
            f"请用WPS打开副本文件查看书签。")

    finally:
        if not success and backup_path and backup_path != original_path:
            import os as _os
            if _os.path.exists(backup_path):
                try:
                    _os.remove(backup_path)
                    print(f"[清理] 已删除副本文件: {backup_path}")
                except Exception:
                    pass


def process_headless(original_path, toc_type, toc_start, toc_end, offset):
    """供 Web 调用的无交互入口，保留 CLI 的验证弹窗"""
    print(f"\n[选中文件] {original_path}")
    print(f"[目录类型] {toc_type}")
    print(f"[目录范围] 第 {toc_start} 页 ~ 第 {toc_end} 页")
    print(f"[正文偏移量] {offset}")

    backup_path = create_backup(original_path)
    if backup_path is None:
        backup_path = original_path
        print(f"[注意] 将在原始文件上直接操作: {backup_path}")
    else:
        clear_bookmarks(backup_path)

    print("\n[1/4] 优先验证最后一个章节的页码...")
    if not find_and_verify_last_chapter(backup_path, toc_end, offset):
        print("  用户终止")
        return None

    entries = None
    if toc_type == 'image':
        print("\n[2/4] 调用大模型图片识别...")
        entries = parse_toc_with_llm_vision(backup_path, toc_start, toc_end)
    else:
        print("\n[2/4] 提取目录文本...")
        raw_text = extract_toc_text(backup_path, toc_start, toc_end)
        print(f"  共 {toc_end - toc_start + 1} 页，文本长度 {len(raw_text)} 字符")

        print("[3/4] 解析目录...")
        entries = parse_toc_locally(raw_text)
        if entries is None:
            print("  本地解析失败，尝试pdfplumber...")
            raw_text2 = extract_toc_text_fallback(backup_path, toc_start, toc_end)
            if raw_text2:
                entries = parse_toc_locally(raw_text2)
        if entries is None:
            print("  pdfplumber未识别到，尝试Edge浏览器...")
            raw_text3 = extract_toc_text_edge(backup_path, toc_start, toc_end)
            if raw_text3:
                entries = parse_toc_locally(raw_text3)
        if entries is None:
            print("  尝试大模型文本识别...")
            entries = parse_toc_with_llm(raw_text)

    if not entries:
        print("未识别到任何章节")
        return None

    print(f"  识别到 {len(entries)} 个章节")
    print("\n[4/4] 写入书签...")
    count = add_bookmarks_to_pdf(backup_path, entries, offset, toc_bookmark_page=toc_start)
    print(f"  成功写入 {count} 个书签")
    print(f"RESULT_PATH:{backup_path}")
    return backup_path


if __name__ == "__main__":
    main()