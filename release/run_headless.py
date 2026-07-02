"""CLI 入口，供 Web 调用"""
import sys, os, traceback

class Tee:
    def __init__(self, f1, f2):
        self.f1 = f1
        self.f2 = f2
    def write(self, s):
        self.f1.write(s)
        self.f2.write(s)
        self.f1.flush()
        self.f2.flush()
    def flush(self):
        self.f1.flush()
        self.f2.flush()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
log_file = sys.argv[6] if len(sys.argv) > 6 else None

if log_file:
    f = open(log_file, 'w', encoding='utf-8')
    sys.stdout = Tee(f, sys.__stdout__)

def log(msg):
    print(msg, flush=True)

try:
    from pdf_bookmarker import process_headless
    log(f"开始处理: {sys.argv[1]}")
    result = process_headless(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
    if result:
        log(f"RESULT_PATH:{result}")
    else:
        log("EXIT:FAILED")
except Exception as e:
    log(f"[错误] {e}")
    traceback.print_exc()
    log("EXIT:FAILED")
finally:
    log("===处理完毕===")
    if log_file:
        f.close()