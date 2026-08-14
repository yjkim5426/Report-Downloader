import os
import re
import json
import datetime
import urllib.parse
import requests
import pandas as pd
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# 1. 구글 드라이브 서비스 인증
def get_gdrive_service():
    sa_json_str = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON')
    if not sa_json_str:
        raise ValueError("GDRIVE_SERVICE_ACCOUNT_JSON 환경변수가 설정되지 않았습니다.")
    
    sa_info = json.loads(sa_json_str)
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

# 2. 구글 드라이브 내 폴더 생성 또는 조회
def get_or_create_folder(service, folder_name, parent_id):
    query = f"name = '{folder_name}' and '{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = res.get('files', [])
    if files:
        return files[0]['id']
    
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    folder = service.files().create(body=file_metadata, fields='id').execute()
    return folder.get('id')

# 3. 구글 드라이브 업로드
def upload_file_to_gdrive(service, file_path, file_name, folder_id):
    query = f"name = '{file_name}' and '{folder_id}' in parents and trashed = false"
    res = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    if res.get('files', []):
        print(f"[스킵] 이미 업로드됨: {file_name}")
        return

    media = MediaFileUpload(file_path, mimetype='application/pdf', resumable=True)
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    print(f"[업로드 완료] {file_name}")

# 4. 와이즈리포트 전일자 요약 HTML 가져오기 (&dt=YYYYMMDD 적용)
def fetch_wisereport_summary(session, cn_type, target_date_str):
    url = f"https://comp.wisereport.co.kr/wiseReport/summary/ReportSummary.aspx?cn={cn_type}&fmt=1&dt={target_date_str}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    }
    res = session.get(url, headers=headers, timeout=10)
    for enc in ['utf-8', 'cp949', 'euc-kr']:
        try:
            return res.content.decode(enc)
        except Exception:
            continue
    return res.text

# 5. 네이버 증권 - 기업 리포트 탐색
def fetch_company_naver(session, headers, code, company_name, broker, author, title):
    base_url = "https://finance.naver.com/research/company_list.naver"
    for page in range(1, 4):
        params = {"searchType": "itemCode", "itemCode": code, "page": page}
        try:
            res = session.get(base_url, params=params, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='type_1')
            if table:
                for tr in table.find_all('tr'):
                    a_pdf = tr.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if a_pdf:
                        row_text = tr.get_text(strip=True)
                        pdf_url = a_pdf['href']
                        if not pdf_url.startswith('http'):
                            pdf_url = "https://ssl.pstatic.net/imgstock/" + pdf_url.lstrip('/')
                        clean_words = [w for w in re.findall(r'\w+', title) if len(w) > 1]
                        if not clean_words or any(w in row_text for w in clean_words[:3]):
                            return pdf_url
                        elif page == 1:
                            candidate_url = pdf_url
                if 'candidate_url' in locals():
                    return candidate_url
        except Exception:
            pass
            
    if company_name:
        encoded_cmp = urllib.parse.quote(company_name, encoding='euc-kr')
        search_url = f"https://finance.naver.com/research/company_list.naver?searchType=itemname&entity={encoded_cmp}"
        try:
            res = session.get(search_url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='type_1')
            if table:
                a_pdf = table.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                if a_pdf:
                    pdf_url = a_pdf['href']
                    if not pdf_url.startswith('http'):
                        pdf_url = "https://ssl.pstatic.net/imgstock/" + pdf_url.lstrip('/')
                    return pdf_url
        except Exception:
            pass
    return None

# 6. 네이버 증권 - 산업 리포트 탐색
def fetch_industry_naver(session, headers, industry_name, broker, author, title):
    base_url = "https://finance.naver.com/research/industry_list.naver"
    first_author = author.split(',')[0].strip() if author else ""
    broker_clean = broker.replace("투자증권", "").replace("증권", "").strip() if broker else ""

    for page in range(1, 6):
        params = {"page": page}
        try:
            res = session.get(base_url, params=params, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='type_1')
            if table:
                for tr in table.find_all('tr'):
                    a_pdf = tr.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if a_pdf:
                        row_text = tr.get_text(strip=True)
                        pdf_url = a_pdf['href']
                        if not pdf_url.startswith('http'):
                            pdf_url = "https://ssl.pstatic.net/imgstock/" + pdf_url.lstrip('/')
                        broker_match = broker_clean and (broker_clean in row_text)
                        author_match = first_author and (first_author in row_text)
                        clean_words = [w for w in re.findall(r'\w+', title) if len(w) > 1]
                        title_match = clean_words and any(w in row_text for w in clean_words[:2])
                        if broker_match and (author_match or title_match):
                            return pdf_url
                        elif author_match and title_match:
                            return pdf_url
        except Exception:
            pass

    search_combos = []
    if broker and first_author:
        search_combos.append(f"{broker} {first_author}")
    elif first_author:
        search_combos.append(first_author)
        
    for combo in search_combos:
        encoded_kw = urllib.parse.quote(combo, encoding='euc-kr')
        search_url = f"https://finance.naver.com/research/industry_list.naver?searchType=keyword&entity={encoded_kw}"
        try:
            res = session.get(search_url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='type_1')
            if table:
                for tr in table.find_all('tr'):
                    a_pdf = tr.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if a_pdf:
                        pdf_url = a_pdf['href']
                        if not pdf_url.startswith('http'):
                            pdf_url = "https://ssl.pstatic.net/imgstock/" + pdf_url.lstrip('/')
                        return pdf_url
        except Exception:
            pass
    return None

# 7. 네이버 증권 - 정기/시황 리포트 탐색
def fetch_regular_naver(session, headers, broker, author, title):
    base_url = "https://finance.naver.com/research/market_info_list.naver"
    first_author = author.split(',')[0].strip() if author else ""
    broker_clean = broker.replace("투자증권", "").replace("증권", "").strip() if broker else ""

    for page in range(1, 6):
        params = {"page": page}
        try:
            res = session.get(base_url, params=params, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='type_1')
            if table:
                for tr in table.find_all('tr'):
                    a_pdf = tr.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if a_pdf:
                        row_text = tr.get_text(strip=True)
                        pdf_url = a_pdf['href']
                        if not pdf_url.startswith('http'):
                            pdf_url = "https://ssl.pstatic.net/imgstock/" + pdf_url.lstrip('/')
                        broker_match = broker_clean and (broker_clean in row_text)
                        author_match = first_author and (first_author in row_text)
                        clean_words = [w for w in re.findall(r'\w+', title) if len(w) > 1]
                        title_match = clean_words and any(w in row_text for w in clean_words[:2])
                        if broker_match and (author_match or title_match):
                            return pdf_url
                        elif author_match and title_match:
                            return pdf_url
        except Exception:
            pass
    return None

# 8. 한경 컨센서스 통합 탐색
def fetch_from_hankyung(session, headers, search_keyword, broker, author, title, rpt_type="COMPANY", target_date_str=""):
    try:
        hk_url = "http://consensus.hankyung.com/apps.analysis/analysis.list"
        first_author = author.split(',')[0].strip() if author else ""
        
        target_dt = datetime.datetime.strptime(target_date_str, "%Y%m%d") if target_date_str else datetime.datetime.now()
        sdate = (target_dt - datetime.timedelta(days=15)).strftime("%Y-%m-%d")
        edate = target_dt.strftime("%Y-%m-%d")

        search_queries = []
        if broker and first_author:
            search_queries.append(f"{broker} {first_author}")
        elif first_author:
            search_queries.append(first_author)
        if search_keyword:
            search_queries.append(f"{broker} {search_keyword}".strip())

        for query in search_queries:
            params = {
                "sdate": sdate,
                "edate": edate,
                "search_text": query,
                "now_page": 1
            }
            if rpt_type == "INDUSTRY":
                params["skin_type"] = "industry"
            elif rpt_type == "REGULAR":
                params["skin_type"] = "market"

            res = session.get(hk_url, params=params, headers=headers, timeout=6)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table')
            if table:
                for tr in table.find_all('tr'):
                    row_text = tr.get_text(strip=True)
                    a_pdf = tr.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if a_pdf:
                        pdf_link = a_pdf['href']
                        if not pdf_link.startswith('http'):
                            pdf_link = "http://consensus.hankyung.com" + pdf_link
                        broker_clean = broker.replace("투자증권", "").replace("증권", "").strip() if broker else ""
                        if (broker_clean and broker_clean in row_text) or (first_author and first_author in row_text) or any(w in row_text for w in re.findall(r'\w+', title) if len(w) > 1):
                            return pdf_link
    except Exception:
        pass
    return None

def main():
    root_folder_id = os.environ.get('GDRIVE_FOLDER_ID')
    if not root_folder_id:
        raise ValueError("GDRIVE_FOLDER_ID 환경변수가 설정되지 않았습니다.")
    
    gdrive_service = get_gdrive_service()
    
    # 한국 시간 기준 '어제(전일자)' 날짜 계산
    kst_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    target_date = kst_now - datetime.timedelta(days=1)
    target_date_str = target_date.strftime("%Y%m%d")

    print(f"=== [실행 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}] ===")
    print(f"=== [수집 대상 일자: {target_date.strftime('%Y-%m-%d')} ({target_date_str})] ===")

    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}

    # 1: 기업, 2: 산업, 3: 정기
    tasks = [
        ('1', 'COMPANY', '기업리포트_원본'),
        ('2', 'INDUSTRY', '산업리포트_원본'),
        ('3', 'REGULAR', '정기리포트_원본')
    ]

    os.makedirs("./temp_downloads", exist_ok=True)

    for cn_type, rpt_type, folder_tag in tasks:
        content_str = fetch_wisereport_summary(session, cn_type, target_date_str)
        soup = BeautifulSoup(content_str, 'html.parser')
        rows = [[td.get_text(strip=True) for td in tr.find_all(['td', 'th'])] for tr in soup.find_all('tr')]

        parsed_reports = []
        for r in rows:
            if not r or len(r) < 2:
                continue
            row_text = " ".join(r)
            if any(k in row_text for k in ["기관명", "산업명", "기업명", "리포트서머리"]):
                continue

            broker_raw = r[0] if r else ""
            m_ba = re.search(r'^(.*?(?:투자증권|증권|선물|리서치|자산운용))(.*)$', broker_raw)
            broker = m_ba.group(1).strip() if m_ba else broker_raw.strip()
            author = m_ba.group(2).strip() if m_ba else ""
            code_match = re.search(r'\[(\d{6})\]', row_text)

            if rpt_type == 'REGULAR':
                category = r[1] if len(r) > 1 else "시황/정기"
                title_candidates = [c.strip() for c in r[2:] if c.strip() and not c.strip().startswith("▶") and c.strip() not in ["BUY", "Buy", "매수", "중립", "=", "▲", "▼", "N", "HOLD", "Overweight", "Positive"]]
                title = title_candidates[-1] if title_candidates else category
                parsed_reports.append({'type': 'REGULAR', '기업명': '', '종목코드': '', '산업명': category, '발행사': broker or "증권사", '작성자': author, '제목': title})
            elif rpt_type == 'COMPANY' and code_match:
                code = code_match.group(1)
                cmp_name = re.sub(r'\[\d{6}\]', '', r[1]).strip() if len(r) > 1 else "기업"
                title = r[7] if len(r) >= 8 and r[7] and not r[7].startswith("▶") else (r[5] if len(r) >= 6 and r[5] and not r[5].startswith("▶") else "")
                if not title:
                    for cell in r[2:]:
                        if cell and not cell.startswith("▶") and not cell.replace(',', '').replace('.', '').isdigit() and cell not in ["BUY", "Buy", "매수", "중립", "=", "▲", "▼", "N", "HOLD", "Overweight", "Positive"]:
                            title = cell
                            break
                parsed_reports.append({'type': 'COMPANY', '기업명': cmp_name, '종목코드': code, '산업명': '', '발행사': broker or "증권사", '작성자': author, '제목': title or "제목없음"})
            elif rpt_type == 'INDUSTRY':
                industry_name = r[1] if len(r) > 1 else ""
                title_candidates = [c.strip() for c in r[2:] if c.strip() and not c.strip().startswith("▶") and c.strip() not in ["BUY", "Buy", "매수", "중립", "=", "▲", "▼", "N", "HOLD", "Overweight", "Positive", "비중확대", "Underweight"]]
                title = title_candidates[-1] if title_candidates else ""
                if title:
                    parsed_reports.append({'type': 'INDUSTRY', '기업명': '', '종목코드': '', '산업명': industry_name, '발행사': broker, '작성자': author, '제목': title})

        if not parsed_reports:
            print(f"[{folder_tag}] ({target_date_str}) 수집 대상 리포트 없음.")
            continue

        target_folder_name = f"{target_date_str}_{folder_tag}"
        gdrive_target_folder_id = get_or_create_folder(gdrive_service, target_folder_name, root_folder_id)
        print(f"\n--- [{target_folder_name}] 다운로드 및 업로드 진행 (총 {len(parsed_reports)}건) ---")

        for rpt in parsed_reports:
            code, company_name, industry_name = rpt['종목코드'], rpt['기업명'], rpt['산업명']
            title, broker, author = rpt['제목'], rpt['발행사'], rpt['작성자']

            try:
                pdf_url = None
                if rpt['type'] == 'COMPANY':
                    pdf_url = fetch_company_naver(session, headers, code, company_name, broker, author, title)
                    if not pdf_url:
                        pdf_url = fetch_from_hankyung(session, headers, company_name, broker, author, title, rpt_type='COMPANY', target_date_str=target_date_str)
                elif rpt['type'] == 'INDUSTRY':
                    pdf_url = fetch_industry_naver(session, headers, industry_name, broker, author, title)
                    if not pdf_url:
                        pdf_url = fetch_from_hankyung(session, headers, industry_name, broker, author, title, rpt_type='INDUSTRY', target_date_str=target_date_str)
                else: # REGULAR
                    pdf_url = fetch_regular_naver(session, headers, broker, author, title)
                    if not pdf_url:
                        pdf_url = fetch_from_hankyung(session, headers, title, broker, author, title, rpt_type='REGULAR', target_date_str=target_date_str)

                if pdf_url:
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
                    target_save_name = company_name if rpt['type'] == 'COMPANY' else (industry_name if rpt['type'] == 'INDUSTRY' else (industry_name or "정기"))
                    safe_target = re.sub(r'[\\/*?:"<>|]', "", target_save_name).strip()
                    safe_broker = re.sub(r'[\\/*?:"<>|]', "", broker).strip()

                    file_name = f"{target_date_str}_{safe_target}_{safe_title[:30]}_{safe_broker}.pdf"
                    local_path = os.path.join("./temp_downloads", file_name)

                    pdf_res = session.get(pdf_url, headers=headers, timeout=12)
                    if pdf_res.status_code == 200:
                        with open(local_path, "wb") as f:
                            f.write(pdf_res.content)
                        # 구글 드라이브 업로드
                        upload_file_to_gdrive(gdrive_service, local_path, file_name, gdrive_target_folder_id)
                        os.remove(local_path)
            except Exception as e:
                print(f"다운로드 실패 [{broker}] {title}: {e}")

    print(f"\n=== 전일자({target_date_str}) 전체 수집 및 구글 드라이브 동기화 완료 ===")

if __name__ == "__main__":
    main()
