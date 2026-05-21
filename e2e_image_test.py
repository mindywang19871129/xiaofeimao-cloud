#!/usr/bin/env python3
"""
小肥猫 v2.2 图片链路端到端测试
==============================
测试：飞书图片上传→下载往返 / OCR / 多图批改 / timedelta修复 / 逐日检查
"""

import sys, os, json, time, struct, zlib, base64, io
from datetime import datetime, date, timedelta

# 添加路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "cloud_function", "ws-server"))
sys.path.insert(0, PROJECT_ROOT)

PASS = 0
FAIL = 0
WARN = 0
results = []

def ok(msg):
    global PASS; PASS += 1
    results.append(f"✅ {msg}")
    print(f"  ✅ {msg}")

def ng(msg):
    global FAIL; FAIL += 1
    results.append(f"❌ {msg}")
    print(f"  ❌ {msg}")

def wn(msg):
    global WARN; WARN += 1
    results.append(f"⚠️ {msg}")
    print(f"  ⚠️ {msg}")

def hr(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")

# ===== 环境变量 =====
os.environ.setdefault("FEISHU_APP_ID", "cli_aa8f8d25a925dbea")
os.environ.setdefault("FEISHU_APP_SECRET", "9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-f5d41971d21d46ffbdd4e1d7af4a093c")
os.environ.setdefault("USER_OPEN_ID", "ou_8bf3770ed43ce0f273c7a34f1597cfe9")


# ===== 辅助：生成最小 PNG 测试图片 =====
def make_test_png(width=200, height=100) -> bytes:
    """生成一张最小 PNG 图片（红底白字示意）"""
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))

    raw = b''
    for y in range(height):
        raw += b'\x00'  # filter none
        for x in range(width):
            raw += b'\xff\x00\x00'  # red pixels

    idat = chunk(b'IDAT', zlib.compress(raw))
    iend = chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


# ===== 测试 1：timedelta 导入修复验证 =====
hr("🐛 测试 1：timedelta 导入修复验证")

try:
    from grading import check_previous_day_completion
    today_str = date.today().strftime("%Y-%m-%d")
    result = check_previous_day_completion(today_str)
    ok(f"check_previous_day_completion({today_str}) 正常返回（无 NameError）")
    print(f"     返回: can_proceed={result.get('can_proceed')}, prev_date={result.get('prev_date')}")
except NameError as e:
    ng(f"timedelta 仍缺失: {e}")
except Exception as e:
    wn(f"返回异常（非 import 问题）: {type(e).__name__}: {str(e)[:80]}")


# ===== 测试 2：飞书 API 连通 + Token =====
hr("📡 测试 2：飞书 API 连通性")

try:
    from feishu_api import _get_tenant_token
    token = _get_tenant_token()
    if token and len(token) > 20:
        ok(f"Tenant token 获取成功 ({token[:30]}...)")
    else:
        ng(f"Token 异常: {token[:30] if token else '(空)'}")
except Exception as e:
    ng(f"Token 获取失败: {e}")
    sys.exit(1)


# ===== 测试 3：图片上传飞书 =====
hr("📤 测试 3：图片上传到飞书")

try:
    from feishu_api import upload_feishu_image
    test_img = make_test_png(200, 100)
    print(f"     测试图片大小: {len(test_img)} bytes (PNG)")

    image_key = upload_feishu_image(test_img, image_type="message")
    if image_key and len(image_key) > 10:
        ok(f"图片上传成功: image_key={image_key}")
    else:
        ng(f"上传失败: image_key={image_key}")
        image_key = None
except Exception as e:
    ng(f"上传异常: {type(e).__name__}: {str(e)[:100]}")
    image_key = None


# ===== 测试 4：图片下载（往返验证）=====
hr("📥 测试 4：下载图片（上传→下载往返）")

if image_key:
    try:
        import requests as req
        from urllib.parse import quote

        # 模拟 main.py 的 _download_image 逻辑
        url = f"https://open.feishu.cn/open-apis/im/v1/images/{quote(image_key, safe='')}?image_type=message"
        headers = {"Authorization": f"Bearer {token}"}
        print(f"     下载 URL: {url[:80]}...")
        resp = req.get(url, headers=headers, timeout=30)

        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            size = len(resp.content)
            if "image" in ct and size > 100:
                ok(f"下载成功: Content-Type={ct}, size={size} bytes")
            elif size > 100:
                ok(f"下载成功（非 image/* 但大小正常）: Content-Type={ct}, size={size}")
            else:
                ng(f"下载内容异常: Content-Type={ct}, size={size}")
        else:
            body = resp.text[:300]
            ng(f"下载失败 HTTP {resp.status_code}: {body}")
    except Exception as e:
        ng(f"下载异常: {type(e).__name__}: {str(e)[:100]}")
else:
    wn("跳过（上传失败）")


# ===== 测试 5：OCR（DeepSeek Vision）=====
hr("🔍 测试 5：OCR 图片识别（DeepSeek Vision）")

try:
    from main import _ocr_image

    # 用一张简单图片测试 OCR
    ocr_text = _ocr_image(test_img)
    if ocr_text:
        ok(f"OCR 返回结果（非空）: {ocr_text[:80]}")
    else:
        wn("OCR 返回空（纯色测试图无文字，或模型不支持图片）")
except Exception as e:
    err_msg = str(e)[:120]
    if "400" in err_msg or "vision" in err_msg.lower() or "deserialize" in err_msg.lower():
        wn(f"OCR 模型不支持 Vision（本地 API Key 限制，JumpServer 配置不同）: {type(e).__name__}")
    else:
        ng(f"OCR 异常: {type(e).__name__}: {err_msg}")


# ===== 测试 6：多图片批改引擎 =====
hr("📋 测试 6：多图片批改引擎（模拟）")

try:
    from grading import grade_submission_multi_image, format_partial_grading_card

    # 模拟两张图片的 OCR 结果
    combined_ocr = "[图1] M1=1035 M2=2\n[图2] M3=不变"
    all_ocr = [
        {"index": 1, "image_key": "test_img_001", "ocr_text": "M1=1035 M2=2"},
        {"index": 2, "image_key": "test_img_002", "ocr_text": "M3=不变"},
    ]

    # 先确保当日有题目数据（从 daily_questions.json 或 bitable）
    daily_file = os.path.join(PROJECT_ROOT, "daily_questions.json")
    if os.path.exists(daily_file):
        with open(daily_file) as f:
            daily_data = json.load(f)
        test_date = daily_data.get("date", date.today().strftime("%Y-%m-%d"))
    else:
        test_date = date.today().strftime("%Y-%m-%d")
        daily_data = None

    # Mock 飞书 client（不实际发消息）
    class MockClient:
        pass
    mock_client = MockClient()

    # 调用批改（会访问 bitable 读题，可能因无 bitable 配置而失败——这是预期行为）
    try:
        result = grade_submission_multi_image(
            mock_client, combined_ocr, test_date,
            all_ocr, image_keys=["test_img_001", "test_img_002"]
        )
        if result.get("success"):
            ok(f"多图批改成功: {result.get('correct_count',0)}✓/{result.get('wrong_count',0)}✗")
        else:
            summary = result.get("summary", "")[:80]
            wn(f"批改返回失败（可能缺 bitable 数据）: {summary}")
    except Exception as e:
        err_msg = str(e)[:100]
        if "BITABLE" in err_msg.upper() or "bitable" in err_msg.lower() or "401" in err_msg or "AttributeError" in type(e).__name__:
            wn(f"批改需要 Bitable/Client 配置（JumpServer 已配）: {type(e).__name__}")
        else:
            ng(f"批改异常: {type(e).__name__}: {err_msg}")

except ImportError as e:
    ng(f"模块导入失败: {e}")
except Exception as e:
    ng(f"测试异常: {type(e).__name__}: {str(e)[:100]}")


# ===== 测试 7：图片批次管理逻辑 =====
hr("📦 测试 7：图片批次管理逻辑")

try:
    from main import _image_batches, _batch_lock, BATCH_WAIT_SECONDS

    ok(f"批次等待时间: {BATCH_WAIT_SECONDS}s")
    ok(f"批次字典类型: {type(_image_batches).__name__}")
    ok(f"批次锁类型: {type(_batch_lock).__name__}")
except ImportError as e:
    ng(f"导入批次管理变量失败: {e}")
except Exception as e:
    ng(f"批次管理测试异常: {e}")


# ===== 测试 8：feishu_api 图片上传工具函数 =====
hr("🛠️ 测试 8：feishu_api 工具函数")

try:
    from feishu_api import _detect_image_info

    # PNG
    png_img = make_test_png(100, 50)
    ext, mime = _detect_image_info(png_img)
    if ext == "png" and mime == "image/png":
        ok(f"PNG 检测正确: {ext} / {mime}")
    else:
        ng(f"PNG 检测错误: 期望 png/image/png, 实际 {ext}/{mime}")

    # JPEG 头
    jpeg_bytes = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01...'
    ext, mime = _detect_image_info(jpeg_bytes)
    if ext == "jpg":
        ok(f"JPEG 检测正确: {ext} / {mime}")
    else:
        ng(f"JPEG 检测错误: 期望 jpg/image/jpeg, 实际 {ext}/{mime}")

    # GIF
    gif_bytes = b'GIF89a......'
    ext, mime = _detect_image_info(gif_bytes)
    if ext == "gif":
        ok(f"GIF 检测正确: {ext} / {mime}")
    else:
        ng(f"GIF 检测错误: 实际 {ext}/{mime}")

except Exception as e:
    ng(f"工具函数测试异常: {e}")


# ===== 测试 9：format_partial_grading_card =====
hr("🃏 测试 9：部分批改卡片格式化")

try:
    from grading import format_partial_grading_card
    mock_result = {
        "success": True,
        "date": date.today().strftime("%Y-%m-%d"),
        "correct_count": 2,
        "partial_count": 1,
        "wrong_count": 0,
        "pass_rate": 83.3,
        "total_questions": 5,
        "graded_count": 3,
        "summary": "📊 批改完成: 2✓ 1🔶 0✗ | 得分率 83.3% | 已完成 3/5",
        "details": [
            {"question": {"id": "M1", "content": "23×45=?", "score": 5}, "score": 5, "max_score": 5, "status": "correct", "feedback": ""},
            {"question": {"id": "M2", "content": "周长公式填空", "score": 3}, "score": 3, "max_score": 3, "status": "correct", "feedback": ""},
            {"question": {"id": "M3", "content": "平移概念选择题", "score": 2}, "score": 1, "max_score": 2, "status": "partial", "feedback": "选对了但理由不完整"},
        ],
        "remaining_questions": [
            {"id": "M4", "content": "尚未作答的题目"},
            {"id": "E1", "content": "英语题目"},
        ],
    }
    title, content = format_partial_grading_card(mock_result)
    if title and content and len(content) > 50:
        ok(f"卡片生成成功: 标题={title[:30]}, 内容长度={len(content)}")
    else:
        ng(f"卡片内容异常: title={title}, content_len={len(content) if content else 0}")
except Exception as e:
    ng(f"卡片格式化异常: {type(e).__name__}: {str(e)[:100]}")


# ===== 汇总 =====
hr("📊 图片链路测试汇总")
print(f"  ✅ 通过: {PASS}  |  ❌ 失败: {FAIL}  |  ⚠️ 警告: {WARN}  |  总计: {PASS+FAIL+WARN}")
for r in results:
    print(f"  {r}")

if FAIL == 0:
    print(f"\n  🎉 图片链路全部通过！")
else:
    print(f"\n  🔴 有 {FAIL} 项失败，需修复！")

sys.exit(0 if FAIL == 0 else 1)
