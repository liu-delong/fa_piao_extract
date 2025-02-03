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


"""
author: liudelong
date: 2025-2-3
description: 本脚本用于提取PDF文件中的发票信息，包括发票类型、发票号码、名称税号、合计金额、备注等。
代码阅读建议：
1. 首先阅读：if __name__ == "__main__": 这个部分，这个部分是程序的入口，是整个程序的主要逻辑。
    它最后调用process_pdf_folder(input_folder)这个函数，这个函数是整个程序的核心，是用来提取PDF文件中的发票信息的。
2. 然后阅读：process_pdf_folder(input_folder) 这个函数。这个函数里面有注释。会引导你读其他函数。
3. 然后阅读field_func_maps(位于process_pdf_folder上方)中罗列的函数:每个函数前面的注释会告诉你这个函数是用来提取什么信息的，
    以及提取信息的方法。
"""
def set_runing_log_output(output_log_file):
    """
    设置同时向命令行和文件输出日志
    """
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
    """
    把字符串中的英文符号替换成中文符号
    """
    s = s.replace("(","（")
    s = s.replace(")","）")
    s = s.replace(":","：")
    s = s.replace(",","，")
    return s

def split_texts(texts):
    """
    把["123456\n7890","发票代码","123455"]拆分成["123456","7890","发票代码","123455"]
    即如果一个字符串中间有不可见字符，则拆分成两个字符串。
    """
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
   """
   辅助函数：获取PDF文件中的文本
   """
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
    """
    辅助函数：打印错误信息
    """
    print(f"~~~~~~~{func_name}_error:{pdf_path}~~~~~~~~")
    print(ocr_texts)
    print("=====ocr_texts↑  pdf_texts↓=====")
    print(pdf_texts)
    print(f"other_info:{other_info}")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

def valid_field(field_value:str,pdf_texts:List[str],pdf_path):
    """
    ocr识别的文本可能会有误差，所以我们需要验证一下识别的文本是否正确。
    field_value是ocr识别的文本，pdf_texts是pdf中的文本。
    格式化：把所有的英文符号替换成中文符号，比如把英文逗号替换成中文逗号，通过这样，可以在比较时忽略中英文符号的差异。
    从pdf_texts中找到field_value中格式化后相同的字符串。如果找不到，返回pdf_texts中最相似的字符串
    
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
    """
    获取备注。
    基本 思想：备注的文本在价税合计和开票人之间，在"备注"这个文本的右边。
    工具：fitz page.get_text("blocks", sort=True) 不仅可以返回文本，还可以返回文本的坐标。
    一个文本的坐标是(x0,y0,x1,y1)，其中(x0,y0)是文本的左上角坐标，(x1,y1)是文本的右下角坐标。
    具体步骤：找到价税合计这个文本的右下角顶点的y坐标:bei_zhu_area_y0
    找到开票人这个文本的左上角顶点的y坐标:bei_zhu_area_y1
    那么备注的文本的y坐标范围就是[bei_zhu_area_y0,bei_zhu_area_y1]
    然后找到备注这两个字的文本的右下角顶点的x坐标:bei_zhu_area_x0
    那么备注的文本的x坐标范围就是[bei_zhu_area_x0,∞)
    在这个范围内的文本就是备注的文本
    """
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
    """
    提取发票号码。
    """
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
    这个是get_ming_cheng_sui_hao的辅助函数。请先了解get_ming_cheng_sui_hao的注释。
    函数功能：确保购买方在前，销售方在后。
    通过文本的x坐标来判断。购买方的x坐标一定小于销售方的x坐标。
    通过get_text("blocks", sort=True)  不仅可以获取文本内容，还可以获取文本的坐标。
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
    """
    提取名称税号,首先通过ocr识别文本。找到名称:xxxxx  xxxx就是我们要的信息。
    找到识别号:xxxxx   xxxx也是我们要的信息。
    我们要找到两个名称和税号。
    但我们并不知道这两个名称和税号哪两个是销售方哪两个是购买方的。
    此时我们可以通过这两个文本的位置来判断。
    我们通过valid_shun_xu函数来确保购买方在前，销售方在后
    """
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
    """
    提取发票类型，直接从pdf_texts中找，不需要ocr
    """
    for text in pdf_texts:
            invoice_type_search = re.search(r'(增值税专用发票|普通发票)', text)
            invoice_type = invoice_type_search.group(1) if invoice_type_search else None
            if invoice_type is not None:
                return invoice_type
    return "未识别发票类型"
def get_he_ji_jin_e2(ocr_texts,pdf_texts:List[str],pdf_path):
    """
    提取合计金额，这个方案是直接在pdf_texts中查找。
    get_text("blocks", sort=True) 不仅返回了文本的内容，还返回了文本的坐标。
    合计金额一般在价税合计这一文本的同一行。
    首先找到价税合计的位置的y坐标(y0,y1)。(y0,y1)表示价税合计的y轴坐标范围，y1减去y0是价税合计这一行的高度
    然后,找y坐标与[y0,y1]有重合的文本。在这些文本中找到￥，它后面的数字就是金额
    有时候，金额可能就在价税合计这一文本里，这在下面关键点1处处理。
    如果找到多个金额，程序无法识别。会返回None
    如果这个范围内只有一个金额，就返回这个金额
    """
    doc = fitz.open(pdf_path)
    page  = doc[-1]
    blocks = page.get_text("blocks", sort=True)
    my_blocks = []
    jia_sui_he_ji_y0= 0  #价税合计的位置
    jia_sui_he_ji_y1 = 0 #价税合计的位置
    for block in blocks:
        x0, y0, x1, y1, text, block_no, block_type = block
        text:str
        text = text.strip()
        text = re.sub(r'\s+', '', text)
        if(text.find("价税合计")!=-1):
            jin_e_search = re.search(r'[¥￥]+([\d,]+\.\d{2})',text)
            if jin_e_search:     # 关键点1：如果价税合计这一行就是金额
                return float(jin_e_search.group(1).replace(",",""))
            jia_sui_he_ji_y0 = y0
            jia_sui_he_ji_y1 = y1
        my_blocks.append((text,x0,y0,x1,y1))  # 关键点2：这里保存了每个文本的内容以及坐标
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
    提取合计金额，这个方案是直接在ocr_texts中查找。
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
}   # 这个用来存放各个信息的提取函数,比如发票类型的提取函数是get_fa_piao_lei_xing，即发票类型用get_fa_piao_lei_xing函数提取

def pdf_infos_to_csv(pdf_infos):
    """
    把识别到的信息写入csv文件
    """
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
            pdf_texts = pdf_texts[-1]  # 只取最后一页
            if pdf_texts == "":
                print(f"{pdf_path}是图片型pdf,请人工识别")
                error_file.write(f"{pdf_path}是图片型pdf,请人工识别")
            res_dict = {t:None for t in field_func_maps}  # 提取到的信息将放在res_dict中
            res_dict["PDF绝对路径"] = os.path.abspath(pdf_path)
            retry_time = 0
            while not all(res_dict.values()) and retry_time < max_retry_time: # 如果有一个字段没有提取到，就重试，最多重试 max_retry_time 次
                retry_time += 1
                ocr_texts = OCR.ocr_pdf(pdf_path)[-1] # ocr, 同样只取最后一页
                for field in res_dict:   # 对于每个信息
                    if res_dict[field] is None:
                        res_dict[field] = field_func_maps[field](ocr_texts,pdf_texts,pdf_path)  #使用这个信息的提取函数。field_func_maps里存了各个信息的提取函数。
            pdf_infos.append(res_dict)
    pdf_infos_to_csv(pdf_infos)
            
    


                

if __name__ == "__main__":

    # 下面4行解析命令行参数，获取发票文件夹路径
    parser = argparse.ArgumentParser(description="Process PDF files to extract invoice information.")
    parser.add_argument("--input_folder", type=str, default=input_folder, help="Path to the input folder containing PDF files.")
    args = parser.parse_args()
    input_folder = args.input_folder

    # 创建输出文件夹
    if not os.path.exists(output_folder):
        os.makedirs(output_folder,exist_ok=True)
    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y_%m_%d_%H_%M_%S")
    if this_time_output_folder is None:
        this_time_output_folder = os.path.join(output_folder,formatted_time)
    os.makedirs(this_time_output_folder,exist_ok=True)

    # 设置同时向命令行和文件输出日志
    all_log_file_name = os.path.join(this_time_output_folder,"all.log")
    f= open(all_log_file_name,"w",encoding="utf8")
    set_runing_log_output(f)  #同时输出到控制台和文件

    # 创建警告和错误日志输出的文件
    warning_file = open(os.path.join(this_time_output_folder,"warning.log"),"w",encoding="utf8")  #警告日志 输出位置
    error_file = open(os.path.join(this_time_output_folder,"error.log"),"w",encoding="utf8")   # 无法识别的文件的输出日志

    

    # 遍历PDF文件夹，提取发票信息
    process_pdf_folder(input_folder)