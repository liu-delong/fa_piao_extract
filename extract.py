from datetime import datetime
import os
import re
import sys
from typing import List
import fitz  # PyMuPDF
import OCR
import difflib
import csv
import argparse

max_retry_time = 5
input_folder = r"发票"  # 输入PDF文件的文件夹路径
output_folder = "outputs"
this_time_output_folder = None  #如果为None 会设置成 output_folder_本次运行时间
def set_runing_log_output(output_log_file):
    class Tee(object):
        def __init__(self, *files):
            self.files = files

        def write(self, obj):
            for f in self.files:
                f.write(obj)
                f.flush()

        def flush(self):
            for f in self.files:
                f.flush()

    tee = Tee(sys.stdout, output_log_file)
    sys.stdout = tee

warning_file = None
error_file = None

def format_str(s:str):
    s = s.replace("(","（")
    s = s.replace(")","）")
    s = s.replace(":","：")
    s = s.replace(",","，")
    return s

def split_texts(texts):
    result = []
    
    # 遍历每个文本
    for text in texts:
        # 如果文本中有空白字符，则进行分割
        if re.search(r'\s', text):  # 检查是否包含空白字符
            # 使用任意空白字符（\s）作为分隔符进行分割
            split_parts = re.split(r'\s+', text)
            result.extend(split_parts)  # 将分割后的部分加入结果
        else:
            result.append(text)  # 没有空白字符的直接加入结果
    
    return result

def get_pdf_texts(pdf_path):
   doc = fitz.open(pdf_path)
   pdf_page_texts = []
   for page in doc:
        blocks = page.get_text("blocks", sort=True)
        texts = []
        for block in blocks:
            x0, y0, x1, y1, text, block_no, block_type = block
            text = text.strip()
            texts.append(text)
        pdf_page_texts.append(texts)
   return pdf_page_texts

def print_error(pdf_path:str,ocr_texts:List[str],pdf_texts:List[str],func_name:str,other_info:str=""):
    print(f"~~~~~~~{func_name}_error:{pdf_path}~~~~~~~~")
    print(ocr_texts)
    print("=====ocr_texts↑  pdf_texts↓=====")
    print(pdf_texts)
    print(f"other_info:{other_info}")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

def valid_field(field_value:str,pdf_texts:List[str],pdf_path):
    """
    从pdf_texts中找到field_value中格式化后相同的字符串。如果找不到，返回pdf_texts中最相似的字符串
    格式化相同：把所有的英文符号替换成中文符号，比如把英文逗号替换成中文逗号
    """
    f_field_value = format_str(str(field_value))
    for text in pdf_texts:
        f_text = format_str(text)
        pos = f_text.find(f_field_value)
        if pos != -1:
            return text[pos:pos+len(f_field_value)]
    new_pdf_texts  = split_texts(pdf_texts)  # pdf_texts中的每行文本中间可能含有空白字符，比如["123456\n7890","发票代码","123455"]，需要拆分成["123456","7890","发票代码","123455"]
    most_like = difflib.get_close_matches(field_value,new_pdf_texts,1,0.8)[0]
    print(f"warning:{pdf_path} change {field_value} to {most_like}")
    warning_file.write(f"{pdf_path} change {field_value} to {most_like}\n")
    return most_like

def get_bei_zhu(ocr_texts,pdf_texts:List[str],pdf_path):
    doc = fitz.open(pdf_path)
    page  = doc[-1]
    blocks = page.get_text("blocks", sort=True)
    my_blocks = []
    bei_zhu_area_y0 = 0
    bei_zhu_area_y1 = 0
    bei_zhu_area_x0 = 0
    bz_text = ""
    for block in blocks:
        x0, y0, x1, y1, text, block_no, block_type = block
        text:str
        text = text.strip()
        if(text.find("价税合计")!=-1):
            bei_zhu_area_y0 = y1
        if(text.find("开票人")!=-1):
            bei_zhu_area_y1 = y0
        if text=="备" or text.replace("\n","") == "备注":
            bei_zhu_area_x0 = x1
        my_blocks.append((text,x0,y0,x1,y1))
    if bei_zhu_area_y0 == 0 or bei_zhu_area_y1 == 0 or bei_zhu_area_x0 == 0:
        print(f"~~~~~~~~get_bei_zhu_error:{pdf_path}~~~~~~~~~~~~")
        print(f"x0,y0,y1 = {bei_zhu_area_x0} {bei_zhu_area_y0} {bei_zhu_area_y1}")
        for b in my_blocks:
            print(b)
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        return ""
    
    for b in my_blocks:
        if b[2] > bei_zhu_area_y0 and b[4] < bei_zhu_area_y1 and b[1] > bei_zhu_area_x0:
            bz_text += b[0].replace("\n","")
    return bz_text

def get_fa_piao_hao_ma(ocr_texts,pdf_texts:List[str],pdf_path):
    text = "".join(ocr_texts)
    invoice_number_search = re.search(r'发票号码[:：]?\s*(\d+)', text)
    invoice_number = invoice_number_search.group(1).strip() if invoice_number_search else None
    if invoice_number is not None:
        res = valid_field(invoice_number,pdf_texts,pdf_path)
        if res is None:
            print_error(pdf_path,ocr_texts,pdf_texts,"get_fa_piao_hao_ma",f"not valid:{invoice_number}")
        return res
    print_error(pdf_path,ocr_texts,pdf_texts,"get_fa_piao_hao_ma",f"can't not find 发票号码")            
    return None

def valid_shun_xu(ming_cheng_shui_hao,pdf_path):
    """
    确保购买方在前，销售方在后
    """
    # 首先找到ming_cheng_shui_hao中4个内容所在的块
    doc = fitz.open(pdf_path)
    page  = doc[-1]
    blocks = page.get_text("blocks", sort=True)
    my_blocks = []
    ming_cheng_shui_hao_shun_xu = [(ming_cheng_shui_hao[0],None),(ming_cheng_shui_hao[1],None),(ming_cheng_shui_hao[2],None),(ming_cheng_shui_hao[3],None)]
    for block in blocks:
        x0, y0, x1, y1, text, block_no, block_type = block
        text:str
        text = text.strip()
        text = re.sub(r'\s+', '', text)
        my_blocks.append((text,x0,y0,x1,y1))
        if(text.find(ming_cheng_shui_hao[0])!=-1):
            ming_cheng_shui_hao_shun_xu[0] = (ming_cheng_shui_hao[0],x0)
        if(text.find(ming_cheng_shui_hao[1])!=-1):
            ming_cheng_shui_hao_shun_xu[1] = (ming_cheng_shui_hao[1],x0)
        if(text.find(ming_cheng_shui_hao[2])!=-1):
            ming_cheng_shui_hao_shun_xu[2] = (ming_cheng_shui_hao[2],x0)
        if(text.find(ming_cheng_shui_hao[3])!=-1):
            ming_cheng_shui_hao_shun_xu[3] = (ming_cheng_shui_hao[3],x0)
    if ming_cheng_shui_hao_shun_xu[0][1] is None or ming_cheng_shui_hao_shun_xu[1][1] is None or ming_cheng_shui_hao_shun_xu[2][1] is None or ming_cheng_shui_hao_shun_xu[3][1] is None:
        print(f"~~~~~~~~valid_shun_xu_error:{pdf_path}~~~~~~~~~~~~")
        print("无法确定名称税号4个字段的顺序")
        for b in my_blocks:
            print(b)
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        return None
    if ming_cheng_shui_hao_shun_xu[0][1] > ming_cheng_shui_hao_shun_xu[2][1]:
        ming_cheng_shui_hao[0], ming_cheng_shui_hao[2] = ming_cheng_shui_hao[2], ming_cheng_shui_hao[0]
    if ming_cheng_shui_hao_shun_xu[1][1] > ming_cheng_shui_hao_shun_xu[3][1]:
        ming_cheng_shui_hao[1], ming_cheng_shui_hao[3] = ming_cheng_shui_hao[3], ming_cheng_shui_hao[1]
    return ming_cheng_shui_hao
def get_ming_cheng_sui_hao(ocr_texts,pdf_texts:List[str],pdf_path):
    text = "\n".join(ocr_texts)
    name_search = re.findall(r'名\s*称[:：]+\s*(\S+)', text)
    tax_id_search = re.findall(r'识别号[:：]?\s*(\S+)', text)
    if len(name_search) == 2 and len(tax_id_search) == 2:
        field_res = [name_search[0].strip(),tax_id_search[0].strip(),name_search[1].strip(),tax_id_search[1].strip()]
        for index in range(len(field_res)):
            res = valid_field(field_res[index],pdf_texts,pdf_path)
            if res is None:
                print_error(pdf_path,ocr_texts,pdf_texts,get_ming_cheng_sui_hao.__name__,f"not valid:{field_res[index]}")
                return None
            else:
                field_res[index] = res
        return valid_shun_xu(field_res,pdf_path)
    else:
        print_error(pdf_path,ocr_texts,pdf_texts,get_ming_cheng_sui_hao.__name__,f"name_search:{name_search} text_id_search:{tax_id_search}")
        return None
    
def get_fa_piao_lei_xing(ocr_texts,pdf_texts:List[str],pdf_path):
    for text in pdf_texts:
            invoice_type_search = re.search(r'(增值税专用发票|普通发票)', text)
            invoice_type = invoice_type_search.group(1) if invoice_type_search else None
            if invoice_type is not None:
                return invoice_type
    return "未识别发票类型"
def get_he_ji_jin_e2(ocr_texts,pdf_texts:List[str],pdf_path):
    doc = fitz.open(pdf_path)
    page  = doc[-1]
    blocks = page.get_text("blocks", sort=True)
    my_blocks = []
    jia_sui_he_ji_y0= 0
    jia_sui_he_ji_y1 = 0
    for block in blocks:
        x0, y0, x1, y1, text, block_no, block_type = block
        text:str
        text = text.strip()
        text = re.sub(r'\s+', '', text)
        if(text.find("价税合计")!=-1):
            jin_e_search = re.search(r'[¥￥]+([\d,]+\.\d{2})',text)
            if jin_e_search:
                return float(jin_e_search.group(1).replace(",",""))
            jia_sui_he_ji_y0 = y0
            jia_sui_he_ji_y1 = y1
        my_blocks.append((text,x0,y0,x1,y1))
    if jia_sui_he_ji_y0 == 0 or jia_sui_he_ji_y1 == 0:
        print(f"~~~~~~~~get_he_ji_jin_e2_error:{pdf_path}~~~~~~~~~~~~")
        print(f"x0,y0,y1 = {jia_sui_he_ji_y0} {jia_sui_he_ji_y1}")
        for b in my_blocks:
            print(b)
        print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        return ""
    c_text = ""
    for b in my_blocks:
        if (b[2] > jia_sui_he_ji_y0 and b[2] < jia_sui_he_ji_y1) or (
            b[4] > jia_sui_he_ji_y0 and b[4] < jia_sui_he_ji_y1) or (
            b[2] < jia_sui_he_ji_y0 and b[4] > jia_sui_he_ji_y1):   # 某个区域的y坐标在价税合计的y坐标范围内
            c_text += b[0]
    jin_e_search = re.findall(r'[¥￥]+([\d,]+\.\d{2})',c_text)
    if len(jin_e_search) == 1:  #如果只找到一个，肯定就是价税合计
        return float(jin_e_search[0].replace(",",""))
    else:   # 找到多个，程序无法识别
        error_file.write(f"{pdf_path} there are {len(jin_e_search)} 价税合计 {jin_e_search}\n")
        return None

def get_he_ji_jin_e(ocr_texts,pdf_texts:List[str],pdf_path):
    """
    用ocr的方案有概率会识别不到，弃用，使用get_he_ji_jin_e2
    """
    text = "".join(ocr_texts)
    total_amount_search = re.search(r'[(（)]+小写[)）]+[:：]?\s*[¥￥]+\s*([\d,]+\.\d{2})', text)
    if total_amount_search:
        amount_str= total_amount_search.group(1)
        amount_valid = valid_field(amount_str,pdf_texts,pdf_path)
        if amount_valid is not None:
            method2_res = get_he_ji_jin_e2(ocr_texts,pdf_texts,pdf_path)
            if method2_res is not None:
                if abs(float(amount_valid.replace(',', '')) - method2_res) > 0.01:
                    print_error(pdf_path,ocr_texts,pdf_texts,get_he_ji_jin_e.__name__,f"method1:{amount_valid} method2:{method2_res}")
            else:
                print(f"method2 is None:{pdf_path}")
            return float(amount_valid.replace(',', ''))
        else:
            print_error(pdf_path,ocr_texts,pdf_texts,get_he_ji_jin_e.__name__,f"not valid:{amount_str}")
            return None
    print_error(pdf_path,ocr_texts,pdf_texts,get_he_ji_jin_e.__name__,f"can't not find (小写)")
    return None


field_func_maps = {
    "发票类型":get_fa_piao_lei_xing,
    "发票号码":get_fa_piao_hao_ma,
    "名称税号":get_ming_cheng_sui_hao,
    "合计金额":get_he_ji_jin_e2,
    "备注":get_bei_zhu,
}

def pdf_infos_to_csv(pdf_infos):
    csv_field_name = ['PDF绝对路径',
        '发票类型',
        '发票号码',
        '发票号码简写',
        '购买方名称',
        '购买方纳税人识别号',
        '销售方名称',
        '销售方纳税人识别号',
        '价税合计金额',
        '备注',
        '合同编号'
    ]
    
    with open(os.path.join(this_time_output_folder,"fa_piao_info.csv"),"w",encoding="utf8",newline="") as file:
        writer =csv.writer(file)
        writer.writerow(csv_field_name)

        for pdf_info in pdf_infos:
            row_data= []
            row_data.append(pdf_info["PDF绝对路径"] if pdf_info["PDF绝对路径"] is not None else "")
            row_data.append(pdf_info["发票类型"] if pdf_info["发票类型"] is not None else "")
            row_data.append(pdf_info["发票号码"] if pdf_info["发票号码"] is not None else "")
            row_data.append(pdf_info["发票号码"][-8:] if pdf_info["发票号码"] is not None else "")
            row_data.append(pdf_info["名称税号"][0] if pdf_info["名称税号"] is not None else "")
            row_data.append(pdf_info["名称税号"][1] if pdf_info["名称税号"] is not None else "")
            row_data.append(pdf_info["名称税号"][2] if pdf_info["名称税号"] is not None else "")
            row_data.append(pdf_info["名称税号"][3] if pdf_info["名称税号"] is not None else "")
            row_data.append(pdf_info["合计金额"] if pdf_info["合计金额"] is not None else "")
            bei_zhu = pdf_info["备注"] if pdf_info["备注"] is not None else ""
            bei_zhu = bei_zhu.strip()
            row_data.append(bei_zhu)
            
            contract_number_search = re.search(r'合同编号[:：]?\s*B\s*S\s*([A-Za-z0-9]+)', bei_zhu)
            if contract_number_search:
                contract_number = contract_number_search.group(1)
            else:
                contract_number = ""
            row_data.append(contract_number)
            writer.writerow(row_data)


def process_pdf_folder(input_folder):
    # 遍历输入文件夹中的所有PDF文件
    pdf_infos = []
    for filename in os.listdir(input_folder):
        if filename.endswith(".pdf"):
            pdf_path = os.path.join(input_folder, filename)
            print(f"===========正在处理PDF文件：{pdf_path}")  
            pdf_texts = get_pdf_texts(pdf_path)
            pdf_texts = pdf_texts[-1]
            if pdf_texts == "":
                print(f"{pdf_path}是图片型pdf,请人工识别")
                error_file.write(f"{pdf_path}是图片型pdf,请人工识别")
            res_dict = {t:None for t in field_func_maps}
            res_dict["PDF绝对路径"] = os.path.abspath(pdf_path)
            retry_time = 0
            while not all(res_dict.values()) and retry_time < max_retry_time:
                retry_time += 1
                ocr_texts = OCR.ocr_pdf(pdf_path)[-1]
                for field in res_dict:
                    if res_dict[field] is None:
                        res_dict[field] = field_func_maps[field](ocr_texts,pdf_texts,pdf_path)
            pdf_infos.append(res_dict)
    pdf_infos_to_csv(pdf_infos)
            
    


                

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process PDF files to extract invoice information.")
    parser.add_argument("--input_folder", type=str, default=input_folder, help="Path to the input folder containing PDF files.")
    args = parser.parse_args()

    input_folder = args.input_folder
    if not os.path.exists(output_folder):
        os.makedirs(output_folder,exist_ok=True)
    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y_%m_%d_%H_%M_%S")
    if this_time_output_folder is None:
        this_time_output_folder = os.path.join(output_folder,formatted_time)
    os.makedirs(this_time_output_folder,exist_ok=True)
    all_log_file_name = os.path.join(this_time_output_folder,"all.log")
    f= open(all_log_file_name,"w",encoding="utf8")
    set_runing_log_output(f)  #同时输出到控制台和文件

    warning_file = open(os.path.join(this_time_output_folder,"warning.log"),"w",encoding="utf8")  #警告日志 输出位置
    error_file = open(os.path.join(this_time_output_folder,"error.log"),"w",encoding="utf8")   # 无法识别的文件的输出日志

    


    process_pdf_folder(input_folder)