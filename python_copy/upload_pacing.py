"""Pace uploads and pause for platform verification; never solve it automatically."""
import re
import time
from playwright.sync_api import Error


def upload_verification_visible(page):
    pattern = re.compile(r'安全验证|请完成验证|请完成下方验证|拖动.{0,12}滑块|操作过于频繁|访问过于频繁')
    for frame in page.frames:
        try:
            if frame != page.main_frame and not frame.frame_element().is_visible():
                continue
            prompts = frame.get_by_text(pattern).all()
            if any(prompt.is_visible() for prompt in prompts):
                return True
        except Error:
            if frame.is_detached():
                continue
            raise
    return False


def pause_for_upload_verification(page):
    if not upload_verification_visible(page):
        return False
    print('检测到平台验证或操作频繁提示，上传已暂停，不会自动重传。', flush=True)
    input('请在浏览器手动完成验证；若提示操作频繁，请等待提示消失，再回到日志窗口按回车继续：')
    if upload_verification_visible(page):
        raise ValueError('平台验证或频繁操作提示仍存在，已停止后续上传')
    return True


def wait_upload_state(page, ready, description, timeout_ms=120000):
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if pause_for_upload_verification(page):
            deadline = time.monotonic() + timeout_ms / 1000
        if ready():
            return
        if time.monotonic() >= deadline:
            raise ValueError(description + '超时；已停止，不会自动重复上传')
        page.wait_for_timeout(500)
