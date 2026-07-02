# bookmark:PDF 自动书签生成工具

自动识别 PDF 目录页内容，为 PDF 文件生成章节目录书签（Outline）。

## 功能特性

- **自动识别目录** — 提取 PDF 目录页文字，结合 LLM 解析章节标题与页码
- **视觉识别模式** — 将目录页渲染为图片，调用视觉大模型（GPT-4o / Qwen VL 等）精准识别
- **CLI + Web 双界面** — 命令行版和浏览器版，适应不同使用场景
- **页码偏移自动计算** — 目录中相对页码 → PDF 实际页码自动映射
- **缺页检测** — 自动验证最后一个章节所在页码是否匹配，防止缺页/多余页
- **罗马数字过滤** — 自动跳过前言等罗马数字页码的章节
- **多引擎回退** — PyMuPDF → pdfplumber → Edge 浏览器 → LLM 四级回退

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

编辑 `config.json`：

```json
{
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "your-api-key-here",
    "llm_model": "deepseek-chat",
    "llm_vision_model": "",
    "vision_base_url": "",
    "vision_api_key": ""
}
```

- `llm_*` — 文本模型配置，用于文字识别回退
- `vision_*` — 视觉模型配置（可选），用于图片识别模式。留空则复用文本模型配置

### 3. 运行

**Web 界面（推荐）：**

```bash
python app_web.py
# 或双击 run_web.bat
```

浏览器自动打开 `http://127.0.0.1:5000`，上传 PDF → 输入页码 → 点击"开始生成书签"。

**命令行：**

```bash
python pdf_bookmarker.py
# 或双击 run.bat
```

## 使用说明

### 输入参数

| 参数 | 说明 | 示例 |
|------|------|------|
| 目录开始页 | PDF 中目录的第一页 | 5 |
| 目录结束页 | PDF 中目录的最后一页 | 9 |
| 正文偏移量 | 正文第1页的 PDF 页码 - 1 | 10（正文从 PDF 第11页开始） |

### 计算公式

```
章节实际页码 = 正文偏移量 + 目录中章节相对页码
```

### 输出

在原始 PDF 同目录下生成 `副本_原文件名.pdf`，包含完整的目录书签。

## 支持的大模型

| 文本模型 | 视觉模型（图片识别） |
|------|------|
| DeepSeek (deepseek-chat) | GPT-4o |
| Qwen (qwen-plus) | Qwen VL (qwen-vl-max) |
| OpenAI (gpt-4o-mini) | Gemini (gemini-2.0-flash) |

> 注意：DeepSeek 暂不支持视觉模式，如需图片识别请使用 GPT-4o 或 Qwen VL。

## 目录结构

```
├── pdf_bookmarker.py    # 核心引擎
├── app_web.py           # Flask Web 后端
├── run_headless.py      # CLI 子进程入口
├── config.json          # 用户配置
├── requirements.txt     # 依赖列表
├── run.bat              # CLI 启动脚本
├── run_web.bat          # Web 启动脚本
└── templates/
    └── index.html       # Web 前端页面
```

## 常见问题

**为什么目录识别不完整？**

建议使用视觉模式（图片识别），文本模式依赖 PDF 文字提取质量，部分 PDF 文字层为乱码。

**为什么某些章节的书签页码有偏差？**

可能原因：
1. PDF 目录页印刷的页码本身不准确
2. 正文偏移量输入有误

程序会自动验证最后一个章节的页码准确性。

**支持哪些 PDF？**

文字型 PDF（可选择自动模式）和扫描版 PDF（需使用视觉模式）。

## License

MIT
