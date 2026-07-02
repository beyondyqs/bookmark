"""
PDF 自动书签生成工具 - Web 界面
启动: python app_web.py
"""
import sys
import os
import json
import time
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, Response
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from pdf_bookmarker import (
    create_backup, clear_bookmarks, extract_toc_text,
    parse_toc_with_llm, parse_toc_with_llm_vision,
    parse_toc_locally, add_bookmarks_to_pdf,
    CONFIG, CONFIG_FILE, save_config,
    extract_toc_text_fallback, extract_toc_text_edge
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

DATA_DIR = Path(__file__).parent / "web_data"
LOG_FILE = Path(__file__).parent / "web_log.txt"

_log_lines = []
_stop_flags = {}
_active_proc = None


@app.route('/api/reset', methods=['POST'])
def reset():
    global _active_proc
    if _active_proc and _active_proc.poll() is None:
        _active_proc.kill()
        _active_proc = None
    _log_lines.clear()
    return jsonify({'status': 'ok'})


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    _log_lines.append(line)
    if len(_log_lines) > 200:
        _log_lines.pop(0)
    print(msg)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        'base_url': CONFIG.get('llm_base_url', ''),
        'api_key': '***' if CONFIG.get('llm_api_key') else '',
        'model': CONFIG.get('llm_model', ''),
        'vision_base_url': CONFIG.get('vision_base_url', '') or CONFIG.get('llm_base_url', ''),
        'vision_api_key': '***' if (CONFIG.get('vision_api_key') or CONFIG.get('llm_api_key')) else '',
        'vision_model': CONFIG.get('llm_vision_model', ''),
        'config_path': str(CONFIG_FILE.resolve()),
    })


@app.route('/api/config', methods=['POST'])
def set_config():
    data = request.get_json(force=True, silent=True) or {}
    api_key_val = (data.get('api_key') or '').strip()
    if api_key_val and api_key_val != '***':
        CONFIG['llm_api_key'] = api_key_val
    if data.get('base_url', '').strip():
        CONFIG['llm_base_url'] = data['base_url'].strip()
    if data.get('model', '').strip():
        CONFIG['llm_model'] = data['model'].strip()
    vision_key_val = (data.get('vision_api_key') or '').strip()
    if vision_key_val and vision_key_val != '***':
        CONFIG['vision_api_key'] = vision_key_val
    if data.get('vision_base_url', '').strip():
        CONFIG['vision_base_url'] = data['vision_base_url'].strip()
    if data.get('vision_model', '').strip():
        CONFIG['llm_vision_model'] = data['vision_model'].strip()
    save_config()
    import json
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        saved = json.load(f)
    return jsonify({
        'status': 'ok',
        'has_text_key': bool(saved.get('llm_api_key')),
        'has_vision_key': bool(saved.get('vision_api_key') or saved.get('llm_api_key')),
        'config_path': str(CONFIG_FILE.resolve()),
    })


@app.route('/api/test_text', methods=['POST'])
def test_text():
    data = request.get_json(force=True, silent=True) or {}
    api_key = data.get('api_key', '') or CONFIG.get('llm_api_key', '')
    base_url = data.get('base_url', '') or CONFIG.get('llm_base_url', '')
    model = data.get('model', '') or CONFIG.get('llm_model', '')

    if not api_key:
        return jsonify({'status': 'error', 'message': '未配置 API Key', 'error_code': 'NoKey'}), 400
    if not model:
        return jsonify({'status': 'error', 'message': '未配置模型名称', 'error_code': 'NoModel'}), 400

    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "正在测试 LLM 服务访问状态，请输出 `OK`，不要附带任何其他内容"}],
            temperature=0, max_tokens=10)
        reply = resp.choices[0].message.content.strip()
        return jsonify({'status': 'ok', 'message': f'连接成功 ({model})', 'reply': reply})
    except Exception as e:
        import traceback
        traceback.print_exc()
        err_msg = str(e)
        if '401' in err_msg or 'Unauthorized' in err_msg:
            err_msg = 'API Key 无效或无权限 (401)'
        elif '404' in err_msg or 'Not Found' in err_msg:
            err_msg = '模型不存在或 API 地址错误 (404)'
        elif 'timeout' in err_msg.lower() or 'timed out' in err_msg.lower():
            err_msg = '连接超时，请检查网络或 API 地址'
        return jsonify({'status': 'error', 'message': err_msg, 'error_code': type(e).__name__}), 500


@app.route('/api/test_vision', methods=['POST'])
def test_vision():
    data = request.get_json(force=True, silent=True) or {}
    vision_model = data.get('vision_model', '') or CONFIG.get('llm_vision_model') or CONFIG.get('llm_model', '')
    vision_url = data.get('vision_url', '') or CONFIG.get('vision_base_url', '') or CONFIG.get('llm_base_url', '')
    vision_key = data.get('vision_api_key', '') or CONFIG.get('vision_api_key', '') or CONFIG.get('llm_api_key', '')

    if not vision_key:
        return jsonify({'status': 'error', 'message': '未配置视觉模型 API Key', 'error_code': 'NoKey'}), 400
    if not vision_model:
        return jsonify({'status': 'error', 'message': '未配置视觉模型名称', 'error_code': 'NoModel'}), 400

    try:
        import base64, io
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (200, 100), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((10, 40), "Hello 123", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        client = OpenAI(base_url=vision_url, api_key=vision_key)
        resp = client.chat.completions.create(
            model=vision_model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "图中写了什么文字？只回复图中文字，不要附带任何其他内容"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]}],
            temperature=0, max_tokens=20)
        reply = resp.choices[0].message.content.strip()
        return jsonify({'status': 'ok', 'message': f'连接成功 ({vision_model})', 'reply': reply})
    except Exception as e:
        import traceback
        traceback.print_exc()
        err_msg = str(e)
        if 'InvalidParameter' in err_msg or 'not both' in err_msg.lower():
            return jsonify({
                'status': 'error',
                'message': f'{vision_model} 不支持图片识别（不支持 text+image 混合输入），请更换为支持视觉的模型（如 gpt-4o）',
                'error_code': 'NotVisionModel'}), 500
        if '401' in err_msg or 'Unauthorized' in err_msg:
            err_msg = 'API Key 无效或无权限 (401)'
        elif '404' in err_msg or 'Not Found' in err_msg:
            err_msg = f'模型 {vision_model} 不存在或 API 地址错误 (404)'
        elif 'timeout' in err_msg.lower() or 'timed out' in err_msg.lower():
            err_msg = '连接超时，请检查网络或 API 地址'
        return jsonify({'status': 'error', 'message': err_msg, 'error_code': type(e).__name__}), 500


@app.route('/api/upload', methods=['POST'])
def upload():
    if 'pdf' not in request.files:
        return jsonify({'error': '未选择文件'}), 400
    pdf = request.files['pdf']
    if pdf.filename == '':
        return jsonify({'error': '文件名为空'}), 400

    session_id = str(int(time.time()))
    # 清理10分钟前的旧数据
    now = time.time()
    if DATA_DIR.exists():
        for old_dir in DATA_DIR.iterdir():
            if old_dir.is_dir() and old_dir.name != session_id:
                try:
                    if now - old_dir.stat().st_mtime > 600:
                        shutil.rmtree(old_dir, ignore_errors=True)
                except Exception:
                    pass
    session_dir = DATA_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    original_path = session_dir / pdf.filename
    pdf.save(str(original_path))
    with open(session_dir / "original_name.txt", "w", encoding="utf-8") as f:
        f.write(pdf.filename)

    return jsonify({
        'session_id': session_id,
        'filename': pdf.filename,
        'path': str(original_path),
    })


@app.route('/api/process', methods=['POST'])
def process():
    data = request.get_json()
    session_id = data.get('session_id')
    toc_type = data.get('toc_type', 'normal')
    toc_start = int(data.get('toc_start', 0))
    toc_end = int(data.get('toc_end', 0))
    offset = int(data.get('offset', 0))
    session_dir = DATA_DIR / (session_id or '')
    pdf_files = list(session_dir.glob('*.pdf')) if session_dir.exists() else []
    path = str(pdf_files[0]) if pdf_files else ''

    if not all([session_id, toc_start, toc_end, path]):
        return jsonify({'error': '参数不完整'}), 400

    def run():
        global _active_proc
        script = Path(__file__).parent / "run_headless.py"
        log_file = session_dir / "process.log"
        args = [sys.executable, "-u", str(script), path, toc_type, str(toc_start), str(toc_end), str(offset), str(log_file)]
        result_file = None
        try:
            flag = subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
            _active_proc = subprocess.Popen(args, creationflags=flag)
            last_pos = 0
            while _active_proc.poll() is None:
                if log_file.exists():
                    with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(last_pos)
                        for line in f:
                            line = line.strip()
                            if line:
                                log(line)
                                if line.startswith('RESULT_PATH:'):
                                    result_file = line.split(':', 1)[1].strip()
                        last_pos = f.tell()
                time.sleep(0.5)
            # 读取剩余输出
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_pos)
                    for line in f:
                        line = line.strip()
                        if line:
                            log(line)
                            if line.startswith('RESULT_PATH:'):
                                result_file = line.split(':', 1)[1].strip()
            if result_file and Path(result_file).exists():
                log(f"RESULT_PATH:{result_file}")
            else:
                log("[错误] 书签生成失败")
                _log_lines.append("ERROR:NO_ENTRIES")
        except Exception as e:
            log(f"[错误] {e}")
            _log_lines.append("ERROR:NO_ENTRIES")
        finally:
            _active_proc = None

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'status': 'started'})


@app.route('/api/log')
def stream_log():
    def generate():
        last_idx = 0
        while True:
            if last_idx < len(_log_lines):
                for i in range(last_idx, len(_log_lines)):
                    yield f"data: {json.dumps({'text': _log_lines[i]})}\n\n"
                last_idx = len(_log_lines)
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/stop/<session_id>', methods=['POST'])
def stop(session_id):
    _stop_flags[session_id] = True
    return jsonify({'status': 'ok'})


@app.route('/api/download/<session_id>')
def download(session_id):
    session_dir = DATA_DIR / session_id
    name_file = session_dir / "original_name.txt"
    download_name = "bookmarked.pdf"
    if name_file.exists():
        download_name = f"副本_{name_file.read_text(encoding='utf-8').strip()}"
    for f in session_dir.glob("副本_*") if session_dir.exists() else []:
        response = send_file(str(f), as_attachment=True, download_name=download_name)
        # 下载后延迟清理
        def cleanup():
            time.sleep(10)
            try:
                shutil.rmtree(session_dir, ignore_errors=True)
            except Exception:
                pass
        threading.Thread(target=cleanup, daemon=True).start()
        return response
    return jsonify({'error': '文件不存在'}), 404


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    url = "http://127.0.0.1:5000"
    print(f"启动 Web 服务: {url}")
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    app.run(debug=False, port=5000, threaded=True)