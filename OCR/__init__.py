import os
import sys
sys.path.append( os.path.dirname(os.path.abspath(__file__)))
import wcocr
import fitz
import threading
def _find_wechat_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    common_paths = os.path.join(script_dir, 'path')
    if os.path.exists(common_paths):
        return common_paths
    else:
        print(f"The path folder does not exist at {common_paths}.")
        return None

def _find_wechatocr_exe():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    wechatocr_path = os.path.join(script_dir, 'path', 'WeChatOCR', 'WeChatOCR.exe')
    if os.path.isfile(wechatocr_path):
        return wechatocr_path
    else:
        print(f"The WeChatOCR.exe does not exist at {wechatocr_path}.")
        return None
_wechat_path = _find_wechat_path()
_wechatocr_path = _find_wechatocr_exe()
if _wechat_path and _wechatocr_path:
    wcocr.init(_wechatocr_path, _wechat_path)
def wechat_ocr(image_path):
    result = wcocr.ocr(image_path)
    texts = []

    for temp in result['ocr_response']:
        text = temp['text']
        if isinstance(text, bytes):
            text = text.decode('utf-8', errors='ignore')
        texts.append(text)

    return texts

def ocr_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    page_texts = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)  # 加载页面
        # 提高图像清晰度，设置分辨率为300dpi
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72),colorspace=fitz.csGRAY)  # 获取页面的像素映射
        image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),f"picture_thread_{threading.current_thread().ident}.png")
        pix.save(image_path)
        texts = wechat_ocr(image_path)
        page_texts.append(texts)
        os.remove(image_path)
    doc.close()
    return page_texts