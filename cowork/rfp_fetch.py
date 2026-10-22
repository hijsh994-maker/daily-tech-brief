# -*- coding: utf-8 -*-
"""[2026-10-22] 국책과제 ① 수집기 — 과기정통부·IITP·IRIS 공고를 **이 서버에서 직접** 받는다.

출처: 사용자 저장소 ai-rfp-fetch/fetch.py (GitHub Actions 에서 돌던 것)를 그대로 가져와 손봤다.
  사용자 지시(10-22): "GitHub Actions 는 이제 필요 없으니까" — 예약 미실행으로 10-22 수집본이 전날 것이었다.
바뀐 점:
  * 기본 사이트 = msit,iitp,iris. 기업마당(bizinfo)은 이 컨테이너의 직접 접속을 끊으므로(ConnectionReset)
    기본에서 뺐다 — 에이전트가 WebFetch 로 목록 1페이지(selectSIIA200View.do?rbsIdx=128)를 연다. `--sites` 로 바꿀 수 있다.
  * 출력 = <저장소>/out/rfp/latest/{meta.json,index.md,items/} + out/rfp/history/<날짜>.md (형식은 종전과 같다).
  * 기본 수집 창 = 3일(--days). 전일·당일 공고를 보는 용도라 7일은 필요 없다.
  * 사이트 단위 재시도 대기 60초 → 20초.
사용법:  bash cowork/run.sh rfp-fetch [--days N] [--sites msit,iitp,iris,bizinfo] [--date YYYY-MM-DD]
의존:    requests olefile pypdf holidays (0단계 pip install 에 포함)
"""
import datetime as dt
import html
import io
import json
import os
import re
import shutil
import struct
import sys
import traceback
import urllib.parse
import zipfile
import zlib
import olefile
import requests

try:
    from pypdf import PdfReader
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # 폰트 경고 억제
except Exception:  # pragma: no cover
    PdfReader = None
try:
    import holidays as _holidays
except Exception:  # pragma: no cover
    _holidays = None

KST = dt.timezone(dt.timedelta(hours=9), "KST")  # 한국은 DST 없음 → 고정 오프셋(tzdata 불필요)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 90
MAX_ATTACH_BYTES = 80 * 1024 * 1024   # 첨부 1개 최대 크기
MAX_TEXT = 60_000                     # 첨부 1개당 저장할 최대 글자수
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(ROOT), "out", "rfp")  # <저장소>/out/rfp
VERIFY = os.environ.get("INSECURE") != "1"
if not VERIFY:
    import urllib3
    urllib3.disable_warnings()

BIZINFO_LIST = ("https://bizinfo.go.kr/sii/siia/selectSIIA200View.do?pblancId=&hashCode=02&rowsSel=6"
                "&rows=50&cpage={page}&cat=&schJrsdCodeTy=&schWntyAt=&schAreaDetailCodes=&schEndAt=N"
                "&orderGb=&sort=&schPblancDiv=01&condition=searchPblancNm&condition1=AND&preKeywords=&keyword=")
BIZINFO_VIEW = "https://bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={id}"
MSIT_LIST = "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=121&mId=311&pageIndex={page}"
MSIT_VIEW = "https://www.msit.go.kr/bbs/view.do"
MSIT_VIEW_LINK = "https://www.msit.go.kr/bbs/view.do?sCode=user&mPid=121&mId=311&nttSeqNo={id}"
MSIT_DOWN = "https://www.msit.go.kr/ssm/file/fileDown.do"
IITP_PAGE = "https://www.iitp.kr/web/lay1/program/S1T44C51/iris/list.do"
IITP_API = "https://www.iitp.kr/link-svc/api/iris/list.do"
IITP_VIEW_LINK = "https://www.iitp.kr/web/lay1/program/S1T44C51/iris/view.do?id={id}"
IRIS_PAGE = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"
IRIS_LIST = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do"
IRIS_VIEW = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={id}"
IRIS_CHECK = "https://www.iris.go.kr/comm/file/retrieveCheckFileDownload.do"
IRIS_DOWN = "https://www.iris.go.kr/comm/file/fileDownload.do"


# ───────────────────────────── 공통 유틸 ─────────────────────────────
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.verify = VERIFY
    # [2026-10-21] 재시도. 10-20 Actions 실행에서 IITP 가 응답 없이 연결을 끊어(RemoteDisconnected)
    #   요청 1회 실패로 그날 IITP 전체가 '수집 실패'가 됐다(10-19·10-21 은 정상 — 일시 장애).
    #   연결 끊김·읽기 오류·429/5xx 를 최대 3회, 5→10→20초 간격으로 다시 시도한다.
    #   IITP·IRIS 목록 조회가 POST 라 POST 도 재시도 대상에 넣는다(조회용이라 중복 부작용 없음).
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "POST", "HEAD"}),
                  raise_on_status=False, respect_retry_after_header=True)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def strip_tags(s: str) -> str:
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</tr>|</li>|</div>|</h\d>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def filename_from_headers(resp, fallback):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if not m:
        return fallback
    name = m.group(1).strip().strip('"')
    try:
        name = urllib.parse.unquote(name)
    except Exception:
        pass
    # requests가 latin-1로 풀어둔 UTF-8 바이트열인 경우 복원
    try:
        name2 = name.encode("latin-1").decode("utf-8")
        if "�" not in name2:
            name = name2
    except Exception:
        pass
    return name or fallback


# ───────────────────────────── 문서 텍스트 추출 ─────────────────────────────
def hwp_text(data: bytes) -> str:
    """HWP 5.0(OLE) 본문 텍스트. 배포용(암호화) 문서는 추출 불가."""
    ole = olefile.OleFileIO(io.BytesIO(data))
    hdr = ole.openstream("FileHeader").read()
    flags = struct.unpack("<I", hdr[36:40])[0]
    if flags & 0x4:
        return "[배포용(암호화) HWP 문서라 텍스트를 추출할 수 없음]"
    compressed = flags & 0x1
    out = []
    secs = sorted([s for s in ole.listdir() if s[0] == "BodyText"],
                  key=lambda s: int(re.sub(r"\D", "", s[-1]) or 0))
    for s in secs:
        raw = ole.openstream(s).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        i = 0
        while i + 4 <= len(raw):
            h = struct.unpack("<I", raw[i:i + 4])[0]
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack("<I", raw[i:i + 4])[0]
                i += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                body = raw[i:i + size]
                j, buf = 0, []
                while j + 2 <= len(body):
                    c = struct.unpack("<H", body[j:j + 2])[0]
                    if c in (10, 13):
                        buf.append("\n"); j += 2
                    elif c < 32:
                        # 1~23(10·13 제외)은 8워드(16바이트)짜리 인라인/확장 컨트롤
                        j += 16 if 1 <= c <= 23 else 2
                        if c in (4, 9, 11, 12, 16):
                            buf.append(" ")
                    else:
                        try:
                            buf.append(body[j:j + 2].decode("utf-16le"))
                        except Exception:
                            pass
                        j += 2
                out.append("".join(buf))
            i += size
    text = "\n".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def hwpx_text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    out = []
    names = sorted([n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)],
                   key=lambda n: int(re.sub(r"\D", "", n)))
    for n in names:
        x = z.read(n).decode("utf-8", "ignore")
        x = re.sub(r"</hp:p>", "\n", x)
        x = re.sub(r"<hp:tab[^>]*/>", "\t", x)
        x = re.sub(r"<[^>]+>", "", x)
        out.append(html.unescape(x))
    text = "\n".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def pdf_text(data: bytes) -> str:
    if PdfReader is None:
        return "[pypdf 미설치로 PDF 추출 불가]"
    r = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages).strip()


def docx_text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    x = z.read("word/document.xml").decode("utf-8", "ignore")
    x = re.sub(r"</w:p>", "\n", x)
    return html.unescape(re.sub(r"<[^>]+>", "", x)).strip()


def extract_text(name: str, data: bytes, depth=0) -> list:
    """[(표시이름, 텍스트 또는 사유)] — zip이면 안쪽 문서별로 펼친다."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    head = data[:4]
    try:
        if head == b"\xd0\xcf\x11\xe0":
            return [(name, hwp_text(data))]
        if head == b"PK\x03\x04" and ext not in ("zip", "docx", "xlsx", "pptx"):
            return [(name, hwpx_text(data))]  # .hwpx, 또는 .hwp 확장자를 단 hwpx
        if ext == "hwp":
            return [(name, "[.hwp 확장자이나 HWP 5.0(OLE) 형식이 아님(HWP 3.0/HTML 등) — 추출 불가]")]
        if ext == "pdf" or head == b"%PDF":
            return [(name, pdf_text(data))]
        if ext == "docx":
            return [(name, docx_text(data))]
        if ext in ("txt", "md"):
            return [(name, data.decode("utf-8", "ignore"))]
        if ext == "zip" and head == b"PK\x03\x04":
            if depth >= 1:
                return [(name, "[중첩 zip은 펼치지 않음]")]
            out = []
            z = zipfile.ZipFile(io.BytesIO(data))
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner = info.filename
                try:  # 한글 파일명(cp437로 잘못 풀린 경우) 복원
                    inner = inner.encode("cp437").decode("cp949")
                except Exception:
                    pass
                iext = inner.rsplit(".", 1)[-1].lower() if "." in inner else ""
                if iext not in ("hwp", "hwpx", "pdf", "docx", "txt", "zip"):
                    out.append((f"{name}/{inner}", "[텍스트 추출 대상 아님 — 건너뜀]"))
                    continue
                out.extend(extract_text(f"{name}/{inner}", z.read(info), depth + 1))
            return out or [(name, "[빈 zip]")]
        return [(name, f"[지원하지 않는 형식(.{ext}) — 건너뜀]")]
    except Exception as e:
        return [(name, f"[추출 실패: {type(e).__name__}: {e}]")]


# ───────────────────────────── 사이트별 수집 ─────────────────────────────
def iso(d):
    return d.strftime("%Y-%m-%d")


def fetch_bizinfo(s, since):
    items = []
    for page in (1, 2, 3):
        h = s.get(BIZINFO_LIST.format(page=page), timeout=TIMEOUT).text
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", h, flags=re.S)
        oldest = None
        for row in rows:
            m = re.search(r"pblancId=(PBLN_\d+)", row)
            if not m:
                continue
            tds = [strip_tags(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
            if len(tds) < 7:
                continue
            reg = re.search(r"\d{4}-\d{2}-\d{2}", tds[6])
            reg = reg.group(0) if reg else ""
            oldest = reg
            if not reg or reg < since:
                continue
            items.append(dict(site="bizinfo", id=m.group(1), title=tds[2], category=tds[1],
                              period=tds[3], ministry=tds[4], agency=tds[5], posted=reg,
                              link=BIZINFO_VIEW.format(id=m.group(1))))
        if oldest and oldest < since:
            break
    for it in items:
        h = s.get(it["link"], timeout=TIMEOUT).text
        seg = h.split('class="view_cont"', 1)[-1].split('modal_pblancNm_title', 1)[0]
        it["body"] = strip_tags(seg)
        atts = []
        for href, name in re.findall(r'href="(/cmm/fms/fileDown\.do\?[^"]+)"[^>]*>(.*?)</a>', h, flags=re.S):
            name = strip_tags(name)
            if not name or "atchFileId" not in href:
                continue
            url = "https://bizinfo.go.kr" + html.unescape(href)
            if any(a["url"] == url for a in atts):
                continue
            atts.append(dict(name=name, url=url))
        it["attachments"] = atts
        download_all(s, it, referer="https://bizinfo.go.kr/")
    return items


def fetch_msit(s, since):
    items = []
    s.get(MSIT_LIST.format(page=1), timeout=TIMEOUT)  # 세션 쿠키
    for page in (1, 2, 3):
        h = s.get(MSIT_LIST.format(page=page), timeout=TIMEOUT).text
        idx_id_title = re.findall(
            r"\$\('#td_'\+'NTT_SJ'\+'_(\d+)'\)\.html\('<a [^']*?fn_detail\(\"?(\d+)\"?\)[^']*?<span>(.*?)</span>", h)
        dates = {}
        for idx, d in re.findall(r"\$\('#td_'\+'REG_DT'\+'_(\d+)'\)\.html\('(\d{4}-\d{2}-\d{2})'\)", h):
            dates.setdefault(idx, d)
        depts = dict(re.findall(r"\$\('#td_'\+'CHRG_DEPT_NM'\+'_(\d+)'\)\.html\('([^']+)'\)", h))
        oldest = None
        for idx, nid, title in idx_id_title:
            reg = dates.get(idx, "")
            oldest = reg or oldest
            if not reg or reg < since:
                continue
            if any(x["id"] == nid for x in items):
                continue
            items.append(dict(site="msit", id=nid, title=html.unescape(strip_tags(title)),
                              dept=depts.get(idx, ""), posted=reg, link=MSIT_VIEW_LINK.format(id=nid),
                              ministry="과학기술정보통신부"))
        if oldest and oldest < since:
            break
    for it in items:
        r = s.post(MSIT_VIEW, data=dict(sCode="user", mPid="121", mId="311", nttSeqNo=it["id"],
                                        pageIndex="1", bbsSeqNo="100"),
                   headers={"Referer": MSIT_LIST.format(page=1)}, timeout=TIMEOUT)
        h = r.text
        seg = h.split('class="view_cont"', 1)[-1].split('class="view_file"', 1)[0]
        it["body"] = strip_tags(seg)
        atts, seen = [], set()
        for no, ord_, ext in re.findall(r"fn_download\('(\d+)',\s*'(\d+)',\s*'(\w+)'\)", h):
            if ext.lower() == "odt" or (no, ord_) in seen:  # 아이콘·버튼 두 앵커가 같은 파일을 가리킴
                continue
            seen.add((no, ord_))
            atts.append(dict(name=f"첨부{ord_}.{ext}", url=MSIT_DOWN, post=dict(atchFileNo=no, fileOrd=ord_, fileBtn="A")))
        it["attachments"] = atts
        download_all(s, it, referer=MSIT_VIEW)
    return items


def fetch_iitp(s, since):
    page = s.get(IITP_PAGE, timeout=TIMEOUT).text
    tok = re.search(r'<meta name="_csrf" content="([^"]+)"', page)
    hdr = re.search(r'<meta name="_csrf_header" content="([^"]+)"', page)
    headers = {"Content-Type": "application/json", "Referer": IITP_PAGE}
    if tok and hdr:
        headers[hdr.group(1)] = tok.group(1)
    items = []
    for cpage in (1, 2):
        r = s.post(IITP_API, headers=headers, timeout=TIMEOUT,
                   data=json.dumps(dict(cms_menu_seq="51", cpage=cpage, rows=30, keyword="", condition="", sort="latest")))
        d = r.json()
        oldest = None
        for it in d.get("list") or []:
            ms = it.get("regist_date")
            reg = iso(dt.datetime.fromtimestamp(ms / 1000, KST)) if ms else ""
            oldest = reg or oldest
            if not reg or reg < since:
                continue
            atts = []
            try:
                for f in json.loads(it.get("file_data") or "[]"):
                    if f.get("name") and f.get("link"):
                        atts.append(dict(name=f["name"], url=f["link"]))
            except Exception:
                pass
            body = strip_tags(it.get("content") or "")
            imgs = re.findall(r'src="([^"]+)"', it.get("content") or "")
            if imgs:
                body = (body + "\n[공고 본문이 이미지로 게시됨: " + ", ".join(imgs) + "]").strip()
            items.append(dict(site="iitp", id=it["id"], title=html.unescape(it.get("title", "")),
                              posted=reg, period=f'{it.get("receipt_begin_date", "")} ~ {it.get("receipt_end_date", "")}',
                              charger=it.get("charger_name", ""), link=IITP_VIEW_LINK.format(id=it["id"]),
                              ministry="과학기술정보통신부", agency="정보통신기획평가원", body=body, attachments=atts))
        if oldest and oldest < since:
            break
    for it in items:
        download_all(s, it, referer=IITP_PAGE)
    return items


def fetch_iris(s, since):
    s.get(IRIS_PAGE, timeout=TIMEOUT)  # 세션 쿠키
    items = []
    for prg, tab in (("ancmIng", "접수중"), ("ancmPre", "접수예정")):
        for page in (1, 2):
            r = s.post(IRIS_LIST, data=dict(ancmPrg=prg, pageIndex=page, pageUnit=50), timeout=TIMEOUT)
            d = r.json()
            lst = d.get("listBsnsAncmBtinSitu") or []
            oldest = None
            for it in lst:
                reg = it.get("ancmDe", "")
                oldest = reg or oldest
                if not reg or reg < since:
                    continue
                if re.match(r"\(?\s*test", it.get("ancmTl", ""), re.I):
                    continue
                if any(x["id"] == it["ancmId"] for x in items):
                    continue
                items.append(dict(site="iris", id=it["ancmId"], tab=tab, title=html.unescape(it.get("ancmTl", "")),
                                  notice_no=it.get("ancmNo", ""), posted=reg,
                                  period=f'{it.get("rcveStrDe", "")} ~ {it.get("rcveEndDe", "")}',
                                  ministry=it.get("blngGovdSeNm", ""), agency=it.get("sorgnNm", ""),
                                  status=it.get("rcveSttSeNmLst", ""), offer_type=it.get("pbofrTpSeNmLst", ""),
                                  link=IRIS_VIEW.format(id=it["ancmId"])))
            if not lst or (oldest and oldest < since):
                break
    for it in items:
        h = s.get(it["link"], timeout=TIMEOUT).text
        seg = h.split('id="content"', 1)[-1]
        seg = re.split(r"<script", seg, maxsplit=1)[0]
        it["body"] = strip_tags(seg)
        atts = []
        for doc, fid, name, size in re.findall(
                r"f_bsnsAncm_downloadAtchFile\('([^']*)','([^']*)','([^']*)'\s*,'(\d+)'\)", h):
            atts.append(dict(name=html.unescape(name), url=IRIS_DOWN,
                             iris=dict(atchDocId=doc, atchFileId=fid), size=int(size)))
        it["attachments"] = atts
        download_all(s, it, referer=it["link"])
    return items


def download_all(s, it, referer):
    for a in it.get("attachments", []):
        try:
            if a.get("post"):
                r = s.post(a["url"], data=a["post"], headers={"Referer": referer}, timeout=TIMEOUT, stream=True)
            elif a.get("iris"):
                s.post(IRIS_CHECK, data=a["iris"], headers={"Referer": referer}, timeout=TIMEOUT)
                r = s.get(a["url"], params=a["iris"], headers={"Referer": referer}, timeout=TIMEOUT, stream=True)
            else:
                r = s.get(a["url"], headers={"Referer": referer}, timeout=TIMEOUT, stream=True)
            r.raise_for_status()
            clen = int(r.headers.get("Content-Length") or 0)
            if clen > MAX_ATTACH_BYTES:
                a["texts"] = [(a["name"], f"[{clen // 1024 // 1024}MB — 너무 커서 건너뜀]")]
                continue
            data = b""
            for chunk in r.iter_content(1 << 20):
                data += chunk
                if len(data) > MAX_ATTACH_BYTES:
                    break
            if len(data) > MAX_ATTACH_BYTES:
                a["texts"] = [(a["name"], "[너무 커서 건너뜀]")]
                continue
            a["name"] = filename_from_headers(r, a["name"])
            a["bytes"] = len(data)
            ctype = r.headers.get("Content-Type", "")
            if data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")) or "text/html" in ctype:
                a["texts"] = [(a["name"], "[다운로드 실패: HTML 응답(로그인/점검 페이지)]")]
                continue
            a["texts"] = extract_text(a["name"], data)
        except Exception as e:
            a["texts"] = [(a.get("name", "?"), f"[다운로드 실패: {type(e).__name__}: {e}]")]


# ───────────────────────────── 출력 ─────────────────────────────
SITE_NAMES = dict(bizinfo="기업마당(bizinfo)", msit="과학기술정보통신부 사업공고", iitp="IITP 사업공고", iris="IRIS 사업공고")
SITE_LINKS = dict(bizinfo=BIZINFO_LIST.format(page=1).replace("rows=50", "rows=15"),
                  msit=MSIT_LIST.format(page=1), iitp=IITP_PAGE, iris=IRIS_PAGE)


def write_item(dirpath, it):
    fn = f'{it["site"]}_{re.sub(r"[^A-Za-z0-9_-]", "", it["id"])}.md'
    lines = [f'# [{SITE_NAMES[it["site"]]}] {it["title"]}', ""]
    for k, label in (("link", "링크"), ("notice_no", "공고번호"), ("posted", "공고일(게시일)"), ("period", "접수기간"),
                     ("tab", "IRIS 탭"), ("status", "공고상태"), ("offer_type", "공모유형"), ("ministry", "소관부처"),
                     ("agency", "전문기관/수행기관"), ("dept", "담당부서"), ("charger", "담당자"), ("category", "분류")):
        if it.get(k):
            lines.append(f"- {label}: {it[k]}")
    lines += ["", "## 상세 페이지 본문", "", it.get("body") or "(본문 없음)", ""]
    atts = it.get("attachments", [])
    lines += [f"## 첨부파일 ({len(atts)}개)", ""]
    for a in atts:
        for name, text in a.get("texts") or [(a["name"], "[미처리]")]:
            text = text or "[추출된 텍스트 없음]"
            trunc = ""
            if len(text) > MAX_TEXT:
                text, trunc = text[:MAX_TEXT], f"\n[... 이하 생략 — 전체 {len(text):,}자 중 {MAX_TEXT:,}자만 저장]"
            lines += [f"### 첨부: {name}", "", text + trunc, ""]
    with open(os.path.join(dirpath, fn), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fn


def main():
    args = sys.argv[1:]
    days, today, sites = 3, None, ["msit", "iitp", "iris"]
    while args:
        a = args.pop(0)
        if a == "--days":
            days = int(args.pop(0))
        elif a == "--date":
            today = dt.date.fromisoformat(args.pop(0))
        elif a == "--sites":
            sites = [x.strip() for x in args.pop(0).split(",") if x.strip()]
        elif a:
            today = dt.date.fromisoformat(a)
    now = dt.datetime.now(KST)
    today = today or now.date()
    since = iso(today - dt.timedelta(days=days))
    hol = None
    if _holidays:
        kr = _holidays.KR(years=[today.year])
        hol = kr.get(today)
    meta = dict(today=iso(today), weekday=["월", "화", "수", "목", "금", "토", "일"][today.weekday()],
                since=since, window_days=days, fetched_at=now.isoformat(timespec="seconds"),
                holiday=dict(is_holiday=bool(hol), name=hol or "",
                             note="holidays(KR) 패키지 기준. 정부가 수시 지정하는 임시공휴일·선거일은 포함되지 않을 수 있음."),
                sites={})

    latest = os.path.join(OUT, "latest")
    shutil.rmtree(latest, ignore_errors=True)
    os.makedirs(os.path.join(latest, "items"), exist_ok=True)
    if "bizinfo" not in sites:
        index_note = "- 기업마당(bizinfo)은 이 서버에서 직접 접속이 끊겨 수집하지 않음 — WebFetch 로 목록을 따로 본다"
    else:
        index_note = ""
    index = [f"# 사업공고 수집 결과 — {iso(today)}({meta['weekday']})", "",
             f"- 수집 시각: {meta['fetched_at']}", f"- 수집 범위: 공고일 {since} ~ {iso(today)} (최근 {days}일)",
             f"- 공휴일 여부(holidays KR): {'공휴일(' + hol + ')' if hol else '평일'}"] + ([index_note] if index_note else []) + [""]
    fetchers = dict(bizinfo=fetch_bizinfo, msit=fetch_msit, iitp=fetch_iitp, iris=fetch_iris)
    for site in sites:
        fn = fetchers[site]
        items = []
        # [2026-10-21] 요청 단위 재시도(session)로도 안 되면 60초 쉬고 새 세션으로 사이트 전체를 한 번 더 시도한다.
        for attempt in (1, 2):
            s = session()
            try:
                items = fn(s, since)
                meta["sites"][site] = dict(ok=True, count=len(items))
                if attempt > 1:
                    meta["sites"][site]["retried"] = True
                log(f"{site}: {len(items)}건" + (" (재시도 성공)" if attempt > 1 else ""))
                break
            except Exception as e:
                meta["sites"][site] = dict(ok=False, count=0, error=f"{type(e).__name__}: {e}")
                log(f"{site}: 실패({attempt}/2) {e}\n{traceback.format_exc()}")
                items = []
                if attempt == 1:
                    import time
                    time.sleep(20)
        index += [f"## {SITE_NAMES[site]}", f"- 목록 URL: {SITE_LINKS[site]}"]
        if not meta["sites"][site]["ok"]:
            index += [f"- **수집 실패**: {meta['sites'][site]['error']}", ""]
            continue
        if not items:
            index += ["- 범위 내 공고 없음", ""]
            continue
        for it in sorted(items, key=lambda x: x["posted"], reverse=True):
            fn_ = write_item(os.path.join(latest, "items"), it)
            it["file"] = f"items/{fn_}"
            att = it.get("attachments", [])
            att_desc = "; ".join(
                f'{a["name"]}' + ("" if any(t and not t.startswith("[") for _, t in a.get("texts", [])) else "(텍스트 없음)")
                for a in att) or "없음"
            extra = []
            for k, lb in (("tab", "IRIS탭"), ("notice_no", "공고번호"), ("status", "상태")):
                if it.get(k):
                    extra.append(f"{lb}: {it[k]}")
            index += [f"### {it['title']}",
                      f"- 공고일: {it['posted']} | 접수기간: {it.get('period', '')} | 기관: {it.get('ministry', '')} / {it.get('agency', it.get('dept', ''))}"
                      + (" | " + " | ".join(extra) if extra else ""),
                      f"- 링크: {it['link']}", f"- 상세 파일: {it['file']}", f"- 첨부({len(att)}): {att_desc}", ""]
    with open(os.path.join(latest, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index))
    with open(os.path.join(latest, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.makedirs(os.path.join(OUT, "history"), exist_ok=True)
    shutil.copy(os.path.join(latest, "index.md"), os.path.join(OUT, "history", f"{iso(today)}.md"))
    total = sum(v["count"] for v in meta["sites"].values())
    print(f"{iso(today)} {total}건 " + " ".join(f'{k}={v["count"] if v["ok"] else "ERR"}' for k, v in meta["sites"].items()))


if __name__ == "__main__":
    main()
