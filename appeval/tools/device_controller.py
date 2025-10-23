import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple, Union
from contextlib import suppress

import pyautogui
import pyperclip
import uiautomator2 as u2
# from metagpt.logs import logger
from loguru import logger

# Windows-only imports guarded to allow Linux/Ubuntu usage
try:
    from pywinauto import Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.win32structures import RECT

    _HAS_PYWINAUTO = True
except Exception:  # pragma: no cover - absence on non-Windows
    Desktop = None  # type: ignore
    UIAWrapper = object  # type: ignore

    class RECT:  # type: ignore
        pass

    _HAS_PYWINAUTO = False

# Linux-only imports (AT-SPI) guarded to allow Windows usage
try:
    import pyatspi  # type: ignore

    _HAS_PYATSPI = True
except Exception:  # pragma: no cover - absence on non-Linux
    _HAS_PYATSPI = False

# Playwright imports (optional, only required for web automation)
try:
    from playwright.async_api import async_playwright
    _HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover - absence on non-web platforms
    _HAS_PLAYWRIGHT = False


class BaseController:
    """Base device controller class

    Provides common functionality for Android and PC controllers.
    """

    def get_screenshot(self, filepath: str = "./screenshot/screenshot.jpg") -> None:
        """Take a screenshot

        Args:
            filepath: Path to save the screenshot
        """
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            self._take_screenshot(filepath)
            logger.info(f"Screenshot saved to: {filepath}")
        except Exception as e:
            logger.error(f"Screenshot failed: {str(e)}")

    def _take_screenshot(self, filepath: str) -> None:
        """Implementation method for taking screenshots, to be implemented by subclasses"""
        raise NotImplementedError

    def run_action(self, action: str) -> None:
        """Execute action

        Args:
            action: Action description string
        """
        logger.info(f"Executing action: {action}")
        # Use list to maintain action order
        action_handlers = [
            ("Run", lambda x: hasattr(self, "_handle_run") and self._handle_run(x)),
            ("Tell", lambda x: hasattr(self, "_handle_tell") and self._handle_tell(x)),
        ]

        for action_type, handler in action_handlers:
            if action_type in action:
                handler(action)
                break

    def _handle_tell(self, action: str) -> None:
        """Handle 'Tell' action"""
        # Get text from action
        text = self._extract_code(action)
        logger.info(f"Handling 'Tell' action: {text}")

    def _extract_code(self, action: str) -> str:
        """Extract code from action string

        Args:
            action: Action string

        Returns:
            str: Extracted code
        """
        start = action.find("(")
        end = action.rfind(")")
        if start != -1 and end != -1 and end > start:
            code = action[start + 1 : end]
            return code.strip("```").replace("\n", "; ")
        return ""

    @staticmethod
    def _contains_chinese(text: str) -> bool:
        """Check if text contains Chinese characters

        Args:
            text: Text to check

        Returns:
            bool: Whether text contains Chinese characters
        """
        return any("\u4e00" <= char <= "\u9fff" for char in text)


class AndroidController(BaseController):
    """Android device controller class

    Provides basic operations for Android devices, including clicking, swiping, input, etc.
    """

    def __init__(self):
        """Initialize Android controller"""
        try:
            self.device = u2.connect()  # Connect device
            u2.enable_pretty_logging()
            self.device.set_input_ime(False)  # Switch input method
        except Exception as e:
            logger.error(f"Failed to initialize Android controller: {str(e)}")
            raise

    def _take_screenshot(self, filepath: str) -> None:
        """Implement screenshot function for Android device"""
        self.device.screenshot(filepath)

    def get_screen_xml(self, location_info: str = "center") -> List[Dict]:
        """Get screen XML information

        Args:
            location_info: Location information format ('center' or 'bbox')

        Returns:
            List[Dict]: List containing element information
        """
        result = []
        screen_height = self.device.window_size()[1]
        xml = self.device.dump_hierarchy()
        root = ET.fromstring(xml)

        def get_element_text(element: ET.Element) -> str:
            """Recursively get element text"""
            if element.attrib.get("text"):
                return element.attrib.get("text")
            for child in element:
                text = get_element_text(child)
                if text:
                    return text
            return ""

        for elem in root.iter():
            elem_class = elem.attrib.get("class", "")
            clickable = elem.attrib.get("clickable", "false")
            focusable = elem.attrib.get("focusable", "false")
            elem_text = get_element_text(elem)
            elem_id = elem.attrib.get("resource-id", "")
            elem_desc = elem.attrib.get("content-desc", "")

            bounds = elem.attrib.get("bounds", "")
            if bounds:
                bounds = bounds.replace("][", ",").replace("[", "").replace("]", "")
                bounds = list(map(int, bounds.split(",")))

                if bounds and (bounds[3] - bounds[1]) > screen_height / 2:
                    continue

                if clickable == "true" or (
                    focusable == "true" and (elem_class == "android.widget.EditText" or elem_class == "android.widget.TextView")
                ):
                    center_x = int((bounds[0] + bounds[2]) / 2)
                    center_y = int((bounds[1] + bounds[3]) / 2)

                    result.append(
                        {
                            "coordinates": [center_x, center_y] if location_info == "center" else bounds,
                            "text": f"Class={elem_class}, Text={elem_text}, ID={elem_id}, Content-desc={elem_desc}, Bounds={bounds}",
                        }
                    )

        return result

    def get_all_packages(self) -> List[str]:
        """Get all installed app package names

        Returns:
            List[str]: List of package names
        """
        return self.device.app_list()

    def get_current_app_package(self) -> str:
        """Get current running app's package name

        Returns:
            str: Current app package name
        """
        return self.device.app_current()["package"]

    def open_app(self, package_name: str) -> bool:
        """Launch application

        Args:
            package_name: Application package name

        Returns:
            bool: Whether launch was successful
        """
        package_name = package_name.split(":")[-1].strip()
        try:
            installed_packages = self.get_all_packages()
            if package_name not in installed_packages:
                logger.error(f"App {package_name} is not installed")
                return False

            self.device.app_start(package_name)
            logger.info(f"Successfully launched app: {package_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to launch app: {str(e)}")
            return False

    def _handle_run(self, action: str) -> None:
        """Handle 'Run' action"""
        code = self._extract_code(action)
        code = code.replace("self.device.tap(", "self.device.click(")
        code = self._add_ime_control(code)
        logger.info(f"Executing code: {code}")
        exec(code)

    def _add_ime_control(self, code: str) -> str:
        """Add input method control to code

        Args:
            code: Original code

        Returns:
            str: Code with input method control added
        """
        matches = re.finditer(r'self\.device\.send_keys\("""(.*?)"""(?:, clear=True)?\);', code)
        modified_code = code
        offset = 0

        for match in matches:
            send_keys = match.group(0)
            new_send_keys = f"self.device.set_input_ime(True); time.sleep(0.5); {send_keys} time.sleep(0.5); self.device.set_input_ime(False);"

            start_index = match.start() + offset
            end_index = match.end() + offset

            modified_code = modified_code[:start_index] + new_send_keys + modified_code[end_index:]
            offset += len(new_send_keys) - len(send_keys)

        return modified_code

class PlaywrightController(BaseController):
    """Playwright async controller.
    
    NOTE: Must be used with await from asyncio context.
    Requires playwright to be installed: pip install playwright
    """

    def __init__(self, cdp_url: str = "http://localhost:9222"):
        """Initialize controller configuration.
        
        Args:
            cdp_url: Chrome DevTools Protocol URL for connecting to existing browser
        
        Raises:
            ImportError: If playwright is not installed
        """
        if not _HAS_PLAYWRIGHT:
            raise ImportError(
                "Playwright is not available. "
                "Install it with: pip install playwright && playwright install chromium"
            )
        
        self.cdp_url = cdp_url
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self._initialized = False
        self._lock = None

    async def initialize(self):
        """Initialize Playwright browser connection (idempotent).
        
        This method can be safely called multiple times.
        First call performs initialization, subsequent calls are no-ops.
        
        Raises:
            ImportError: If playwright is not installed
            RuntimeError: If initialization fails
        """
        # 幂等性检查
        if self._initialized and self.page is not None:
            logger.info("Playwright already initialized, skipping")
            return
        
        # 并发控制
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()
        
        async with self._lock:
            # 双重检查锁定
            if self._initialized and self.page is not None:
                return
            
            try:
                # 启动Playwright
                logger.info("Starting Playwright...")
                self._pw = await async_playwright().start()
                
                # 初始化浏览器
                await self._initialize_browser()
                
                # 标记已初始化
                self._initialized = True
                logger.info(f"Playwright initialized successfully, page URL: {self.page.url}")
                
            except Exception as e:
                logger.error(f"Failed to initialize Playwright: {str(e)}")
                # 清理已创建的资源
                await self._cleanup_resources()
                raise RuntimeError(f"Playwright initialization failed: {str(e)}") from e

    async def _initialize_browser(self):
        """Initialize browser connection via CDP or launch new instance.
        
        Raises:
            Exception: If browser initialization fails
        """
        # 尝试通过CDP连接现有Chrome
        try:
            logger.info(f"Attempting to connect via CDP to {self.cdp_url}")
            self.browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
            
            # 获取或创建context
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
                logger.info(f"Using existing context with {len(self.context.pages)} pages")
            else:
                logger.info("Creating new context in existing browser")
                self.context = await self.browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
            
            # 获取或创建page
            self.page = await self._get_or_create_page()
            
            # 如果页面是空白或 about:blank，导航到百度
            try:
                current_url = self.page.url
                logger.info(f"Current page URL: {current_url}")
                if not current_url or current_url == "about:blank" or current_url.startswith("chrome://"):
                    logger.info("Page is blank, attempting to navigate to Baidu...")
                    await self.page.goto("https://www.baidu.com", wait_until="domcontentloaded", timeout=30000)
                else:
                    logger.info("Page already has content, skipping navigation to Baidu")
            except Exception as e:
                logger.error(f"❌ Navigation to Baidu failed: {e}")
            
            logger.info(f"Connected to existing Chrome via CDP")
            
        except Exception as e:
            logger.warning(f"CDP connection failed: {e}")
            logger.info("Launching new Chromium instance...")
            
            # 清理失败的连接
            if self.browser:
                try:
                    await self.browser.close()
                except Exception:
                    pass
                finally:
                    self.browser = None
                    self.context = None
            
            # 启动新的Chromium实例
            self.browser = await self._pw.chromium.launch(
                headless=False,
                args=['--start-maximized', '--disable-blink-features=AutomationControlled']
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                no_viewport=True
            )
            self.page = await self.context.new_page()
            
            # 默认导航到百度首页
            logger.info("Attempting to navigate to Baidu homepage...")
            try:
                await self.page.goto("https://www.baidu.com", wait_until="domcontentloaded", timeout=30000)
                logger.info(f"✅ Successfully navigated to Baidu homepage: {self.page.url}")
            except Exception as e:
                logger.error(f"❌ Failed to navigate to Baidu: {e}")
                logger.warning("Browser will start with blank page")
            
            logger.info("New Chromium instance launched successfully")

    async def _get_or_create_page(self):
        """Get an existing non-devtools page or create a new one.
        
        Returns:
            Page: A valid Playwright page object
        """
        pages = self.context.pages
        
        # 查找第一个非devtools和非chrome内部页面
        for page in pages:
            try:
                url = page.url
                if not url.startswith("devtools://") and not url.startswith("chrome://"):
                    logger.info(f"Using existing page: {url}")
                    return page
            except Exception as e:
                logger.debug(f"Error checking page URL: {e}")
                continue
        
        # 如果没有找到合适的页面，创建新页面
        logger.info("No suitable existing page found, creating new page")
        return await self.context.new_page()

    async def _cleanup_resources(self):
        """Clean up all Playwright resources.
        
        This method is called when initialization fails or during explicit cleanup.
        """
        logger.debug("Cleaning up Playwright resources...")
        
        # 清理page
        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                logger.debug(f"Error closing page: {e}")
            finally:
                self.page = None
        
        # 清理context
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")
            finally:
                self.context = None
        
        # 清理browser
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            finally:
                self.browser = None
        
        # 清理playwright
        if self._pw:
            try:
                await self._pw.stop()
            except Exception as e:
                logger.debug(f"Error stopping playwright: {e}")
            finally:
                self._pw = None
        
        self._initialized = False
        logger.debug("Playwright resources cleaned up")

    async def async_get_screenshot(self, filepath: str) -> None:
        """Take a screenshot of the current page.
        
        Args:
            filepath: Path where screenshot will be saved
            
        Raises:
            RuntimeError: If page is not initialized
        """
        if not self.page:
            raise RuntimeError("Playwright page is not initialized. Call initialize() first.")
        
        try:
            await self.page.screenshot(path=filepath, full_page=False)
            logger.debug(f"Screenshot saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            raise

    async def async_goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """Navigate to a URL.
        
        Args:
            url: The URL to navigate to
            wait_until: When to consider navigation succeeded. Options:
                       'load', 'domcontentloaded', 'networkidle', 'commit'
                       
        Raises:
            RuntimeError: If page is not initialized
        """
        if not self.page:
            raise RuntimeError("Playwright page is not initialized. Call initialize() first.")
        
        try:
            logger.info(f"Navigating to {url}")
            await self.page.goto(url, wait_until=wait_until, timeout=30000)
            logger.info(f"Successfully navigated to {url}")
        except Exception as e:
            logger.error(f"Failed to navigate to {url}: {e}")
            raise

    async def get_current_url(self) -> str:
        """Get the current page URL.
        
        Returns:
            str: Current URL or empty string if page is not initialized
        """
        if not self.page:
            logger.warning("Playwright page is not initialized")
            return ""
        
        try:
            url = self.page.url
            logger.debug(f"Current URL: {url}")
            return url
        except Exception as e:
            logger.error(f"Failed to get current URL: {e}")
            return ""

    async def arun_action(self, action: str) -> None:
        """Execute a Playwright action from LLM-generated code.
        
        Args:
            action: Action string containing Python code to execute
            
        Raises:
            RuntimeError: If page is not initialized or code execution fails
        """
        if not self.page:
            raise RuntimeError("Playwright page is not initialized. Call initialize() first.")
        
        raw = self._extract_code(action)
        logger.info(f"Executing async code: {raw}")

        # 安全检查
        if not self._is_code_safe(raw):
            raise ValueError(f"Unsafe code detected in action: {raw}")

        # 规范化代码并自动添加await
        statements = [s.strip() for s in raw.split(";") if s.strip()]
        awaited_lines = []
        # Playwright 异步方法关键字（需要自动添加 await）
        keywords = (
            # 导航相关
            "goto(", ".reload(", ".go_back(", ".go_forward(",
            # 元素定位
            "locator(",
            # 鼠标操作
            ".click(", ".dblclick(", ".hover(", ".tap(",
            # 拖拽操作
            ".drag_to(", ".drag_and_drop(",
            # 输入操作
            ".fill(", ".type(", ".clear(", ".input_value(",
            # 键盘操作
            ".press(", ".select_text(",
            # 表单操作
            ".check(", ".uncheck(", ".select_option(", ".set_checked(",
            # 滚动操作
            ".wheel(", ".scroll_into_view_if_needed(",
            # 等待操作
            ".wait_for_load_state(", ".wait_for_selector(", ".wait_for_timeout(",
            # 截图和其他
            ".screenshot(", ".evaluate(", ".evaluate_handle(",
            # Frame 操作
            ".frame(", ".frame_locator(",
            # 获取属性
            ".get_attribute(", ".text_content(", ".inner_text(",
            ".is_visible(", ".is_enabled(", ".is_checked("
        )
        
        for stmt in statements:
            s = stmt
            low = s.replace(" ", "")
            needs_await = (
                not s.startswith("await ")
                and ("self.page." in s or "page." in s)
                and any(k in low for k in keywords)
            )
            if needs_await:
                s = "await " + s
            awaited_lines.append(s)

        # 包装成异步函数
        func_code = (
            "async def __runner(self, page):\n"
            + "    import asyncio, time\n"
            + "\n".join(["    " + line for line in awaited_lines])
            + "\n"
        )
        
        try:
            # 记录执行前的页面数量和当前页面
            import asyncio
            old_pages_count = len(self.context.pages)
            old_page_url = self.page.url
            
            local_env = {}
            # 限制可用的全局变量（允许基本的 import 和常用函数）
            safe_globals = {
                '__builtins__': {
                    '__import__': __import__,  # 允许 import 语句（受 _is_code_safe 限制）
                    # 基本类型
                    'range': range,
                    'len': len,
                    'int': int,
                    'str': str,
                    'float': float,
                    'bool': bool,
                    'list': list,
                    'dict': dict,
                    'tuple': tuple,
                    'set': set,
                    # 数学运算
                    'min': min,
                    'max': max,
                    'abs': abs,
                    'round': round,
                    'sum': sum,
                    'pow': pow,
                    # 字符串和迭代
                    'enumerate': enumerate,
                    'zip': zip,
                    'map': map,
                    'filter': filter,
                    'sorted': sorted,
                    'reversed': reversed,
                    # 类型检查
                    'isinstance': isinstance,
                    'type': type,
                    'hasattr': hasattr,
                    'getattr': getattr,
                    # 调试
                    'print': print,
                }
            }
            exec(func_code, safe_globals, local_env)
            
            # 添加超时保护
            await asyncio.wait_for(
                local_env["__runner"](self, self.page),
                timeout=60.0  # 60秒超时
            )
            
            # 检查是否打开了新标签页
            await asyncio.sleep(0.5)  # 等待新页面加载
            new_pages_count = len(self.context.pages)
            
            if new_pages_count > old_pages_count:
                # 有新标签页打开，切换到最新的页面
                new_page = self.context.pages[-1]
                logger.info(f"🔄 Detected new tab opened. Switching from '{old_page_url}' to '{new_page.url}'")
                self.page = new_page
                # 等待新页面加载完成
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                    logger.info(f"✅ Successfully switched to new tab: {self.page.url}")
                except Exception as e:
                    logger.warning(f"New page load wait timeout (this might be normal): {e}")
            
        except asyncio.TimeoutError:
            logger.error("Action execution timeout (60s)")
            raise RuntimeError("Action execution timeout")
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            raise

    def _is_code_safe(self, code: str) -> bool:
        """Check if code is safe to execute.
        
        Args:
            code: Code string to validate
            
        Returns:
            bool: True if code appears safe, False otherwise
        """
        # 检查危险的操作
        dangerous_patterns = [
            'import os', 'import sys', 'import subprocess',
            '__import__', 'eval(', 'exec(',
            'open(', 'file(', 'input(',
            'compile(', '__builtins__'
        ]
        
        code_lower = code.lower()
        for pattern in dangerous_patterns:
            if pattern.lower() in code_lower:
                logger.warning(f"Dangerous pattern detected: {pattern}")
                return False
        
        return True

    async def aclose(self):
        """Close all Playwright resources gracefully.
        
        This method should be called when done with the controller.
        """
        logger.info("Closing Playwright controller...")
        
        errors = []
        
        # 关闭context
        if self.context:
            try:
                import asyncio
                await asyncio.wait_for(self.context.close(), timeout=5.0)
                logger.debug("Context closed")
            except asyncio.TimeoutError:
                logger.warning("Context close timeout")
            except Exception as e:
                logger.error(f"Error closing context: {e}")
                errors.append(e)
            finally:
                self.context = None
        
        # 关闭browser
        if self.browser:
            try:
                import asyncio
                await asyncio.wait_for(self.browser.close(), timeout=5.0)
                logger.debug("Browser closed")
            except asyncio.TimeoutError:
                logger.warning("Browser close timeout")
            except Exception as e:
                logger.error(f"Error closing browser: {e}")
                errors.append(e)
            finally:
                self.browser = None
        
        # 停止playwright
        if self._pw:
            try:
                import asyncio
                await asyncio.wait_for(self._pw.stop(), timeout=5.0)
                logger.debug("Playwright stopped")
            except asyncio.TimeoutError:
                logger.warning("Playwright stop timeout")
            except Exception as e:
                logger.error(f"Error stopping playwright: {e}")
                errors.append(e)
            finally:
                self._pw = None
        
        self._initialized = False
        self.page = None
        
        if errors:
            logger.warning(f"Encountered {len(errors)} errors during cleanup")
        else:
            logger.info("Playwright controller closed successfully")

    async def get_screen_xml(self, location_info: str = "center", max_nodes: int = 400) -> List[Dict]:
        """Get screen XML/DOM information (async method for Playwright).
        
        Note:
            This is an async method. Must be called with await.
        Args:
            location_info: 'center' returns [x,y], 'bbox' returns [x1,y1,x2,y2]
            max_nodes: Maximum number of nodes to collect
        Returns:
            List of dicts with keys 'coordinates' and 'text'
            
        Raises:
            RuntimeError: If page is not initialized
        """
        if not self.page:
            logger.warning("Playwright page is not initialized")
            return []
        
        # ==================== 方法：使用 query_selector_all（原生 Playwright API）====================
        try:
            # 获取所有交互元素和有意义的元素
            selectors = [
                'button', 'a', 'input', 'select', 'textarea',  # 表单和链接
                '[role="button"]', '[role="link"]', '[role="textbox"]', '[role="search"]',  # ARIA 角色
                '[onclick]', '[href]', '[tabindex]',  # 可交互元素
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6',  # 标题
                'p', 'span', 'div',  # 文本容器（保留 div，后续智能过滤）
                'img', 'svg',  # 图片和图标
                'label', 'li'  # 标签和列表项
            ]
            
            # 合并选择器
            combined_selector = ', '.join(selectors)
            elements = await self.page.query_selector_all(combined_selector)
            
            results = []
            processed_count = 0
            seen_elements = set()  # 用于去重：存储元素指纹
            
            # def create_element_fingerprint(text: str, tag: str, role: str, elem_id: str, elem_class: str, coords: list) -> str:
            #     """创建元素指纹用于去重
                
            #     策略：
            #     1. 优先使用 id（唯一标识）
            #     2. 如果有文本，只使用文本内容（忽略标签，避免嵌套元素重复）
            #     3. 如果是交互元素，使用 role + class 组合
            #     4. 其他情况：返回 None，不去重
            #     """
            #     # 策略1: 如果有唯一ID，直接使用
            #     if elem_id:
            #         return f"id:{elem_id}"
                
            #     # 策略2: 如果有文本内容，只使用文本（忽略标签）
            #     # 这样可以过滤 <p>text</p> 和 <a>text</a> 这种嵌套重复
            #     text_stripped = text.strip()
            #     if text_stripped:
            #         # 对于长文本，只取前50个字符作为指纹
            #         text_key = text_stripped[:50] if len(text_stripped) > 50 else text_stripped
            #         return f"text:{text_key}"
                
            #     # 策略3: 无文本但有角色，使用 role + class 组合
            #     if role:
            #         return f"role:{role}|class:{elem_class}"
                
            #     # 策略4: 其他情况（无id、无text、无role的元素）
            #     # 不去重，使用唯一标识（坐标）确保每个都保留
            #     coord_str = f"{coords[0]},{coords[1]}" if len(coords) >= 2 else "0,0"
            #     return f"unique:{coord_str}|{tag}|{elem_class}"
            
            def create_element_fingerprint(text: str, tag: str, role: str, elem_id: str, elem_class: str, coords: list) -> str:
                """创建元素指纹用于去重"""
                # 策略1: ID 优先，ID是唯一的
                if elem_id:
                    return f"id:{elem_id}"
                
                # --- 新增：坐标分桶 ---
                # 定义一个“桶”的大小，例如 50x50 像素
                # 坐标 [100, 100] 和 [105, 105] 都会落入同一个桶
                # 坐标 [100, 100] 和 [500, 500] 会落入不同的桶
                bin_size = 50 
                coord_bin_x = int(coords[0] / bin_size) if len(coords) >= 2 else 0
                coord_bin_y = int(coords[1] / bin_size) if len(coords) >= 2 else 0
                # bin_key 现在是元素的大致位置
                bin_key = f"bin:{coord_bin_x},{coord_bin_y}"
                # ----------------------

                # 策略2: 文本 + 坐标桶
                text_stripped = text.strip()
                if text_stripped:
                    text_key = text_stripped[:50] if len(text_stripped) > 50 else text_stripped
                    # 指纹现在包含内容和位置
                    return f"text:{text_key}|{bin_key}"
                
                # 策略3: 角色 + 坐标桶 (适用于无文本的图标按钮)
                if role:
                    # 指纹现在包含内容和位置
                    return f"role:{role}|{bin_key}"
                
                # 策略4: 标签 + 坐标桶 (最后的防线，取代你原来的策略4)
                return f"tag:{tag}|{bin_key}"
            for elem in elements:
                if processed_count >= max_nodes:
                    break
                
                try:
                    # 检查可见性
                    is_visible = await elem.is_visible()
                    if not is_visible:
                        continue
                    
                    # 获取边界框
                    bbox = await elem.bounding_box()
                    if not bbox:
                        continue
                    
                    # 一次性获取元素所有信息（减少调用次数）
                    info = await elem.evaluate('''
                        el => ({
                            tag: el.tagName,
                            text: (el.innerText || el.textContent || '').trim().slice(0, 120),
                            role: el.getAttribute('role') || '',
                            id: el.id || '',
                            class: (el.className || '').toString().slice(0, 50),
                            name: el.getAttribute('name') || '',
                            hasOnclick: el.hasAttribute('onclick'),
                            childCount: el.children.length,
                            // 获取直接子元素的文本长度
                            childTextLength: Array.from(el.children).reduce((sum, child) => 
                                sum + (child.innerText || child.textContent || '').trim().length, 0)
                        })
                    ''')
                    
                    # 跳过空文本的非交互元素（减少噪音）
                    if not info['text'] and not info['id'] and not info['role'] and info['tag'] in ('SPAN', 'IMG', 'SVG'):
                        continue
                    
                    # DIV 特殊处理：只保留有意义的 DIV
                    if info['tag'] == 'DIV':
                        # 条件1: 有 ID 或 role（可能是交互元素）
                        has_identity = bool(info['id'] or info['role'])
                        
                        # 条件2: 有 onclick（可交互）
                        is_interactive = info.get('hasOnclick', False)
                        
                        # 条件3: 有独立文本内容
                        # 如果 DIV 的文本长度 > 子元素的文本长度，说明 DIV 本身有独立文本
                        # 如果没有子元素但有文本，也保留
                        has_own_text = False
                        if info['text']:
                            text_len = len(info['text'])
                            child_text_len = info.get('childTextLength', 0)
                            child_count = info.get('childCount', 0)
                            # 如果没有子元素，或者文本明显多于子元素，保留
                            has_own_text = (child_count == 0) or (text_len > child_text_len + 5)
                        
                        # 只有满足以上任一条件，才保留 DIV
                        if not (has_identity or is_interactive or has_own_text):
                            continue
                    
                    # 计算坐标
                    if location_info == "center":
                        coords = [
                            int(bbox['x'] + bbox['width'] / 2),
                            int(bbox['y'] + bbox['height'] / 2)
                        ]
                    else:  # bbox
                        coords = [
                            int(bbox['x']), 
                            int(bbox['y']),
                            int(bbox['x'] + bbox['width']),
                            int(bbox['y'] + bbox['height'])
                        ]
                    
                    # 基于内容的去重：创建元素指纹
                    fingerprint = create_element_fingerprint(
                        info['text'], 
                        info['tag'], 
                        info['role'], 
                        info['id'], 
                        info['class'],
                        coords
                    )
                    
                    # 检查是否重复
                    if fingerprint in seen_elements:
                        continue
                    
                    seen_elements.add(fingerprint)
                    
                    # 构建文本信息
                    text_line = f"text={info['text']}; tag={info['tag']}; role={info['role']}; id={info['id']}; class={info['class']}; name={info['name']}"
                    
                    results.append({"coordinates": coords, "text": text_line})
                    processed_count += 1
                    
                except Exception as e:
                    logger.debug(f"Error processing element: {e}")
                    continue
            
            logger.info(f"Collected {len(results)} DOM elements (content-based deduplication) using native Playwright API")
            return results
            
        except Exception as e:
            logger.error(f"Failed to get DOM elements: {e}")
            return []
        # # 构建JavaScript脚本
        # script = (
        #     "() => {\n"
        #     "  const isVisible = (el) => {\n"
        #     "    const style = window.getComputedStyle(el);\n"
        #     "    const rect = el.getBoundingClientRect();\n"
        #     "    if (!rect || rect.width === 0 || rect.height === 0) return false;\n"
        #     "    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity||'1') === 0) return false;\n"
        #     "    return true;\n"
        #     "  };\n"
        #     "  const nodes = Array.from(document.querySelectorAll('*')).slice(0, "
        #     + str(max_nodes)
        #     + ");\n"
        #     "  const out = [];\n"
        #     "  for (const el of nodes) {\n"
        #     "    try {\n"
        #     "      if (!isVisible(el)) continue;\n"
        #     "      const rect = el.getBoundingClientRect();\n"
        #     "      const x1 = Math.max(0, Math.round(rect.left));\n"
        #     "      const y1 = Math.max(0, Math.round(rect.top));\n"
        #     "      const x2 = Math.max(0, Math.round(rect.right));\n"
        #     "      const y2 = Math.max(0, Math.round(rect.bottom));\n"
        #     "      const cx = Math.round((x1 + x2) / 2);\n"
        #     "      const cy = Math.round((y1 + y2) / 2);\n"
        #     "      const role = (el.getAttribute('role')||'');\n"
        #     "      const name = (el.getAttribute('name')||'');\n"
        #     "      const id = (el.id||'');\n"
        #     "      const cls = (el.className||'').toString().slice(0, 50);\n"
        #     "      const txt = (el.innerText||'').trim().slice(0, 120);\n"
        #     "      out.push({ bbox: [x1,y1,x2,y2], center: [cx,cy], meta: { tag: el.tagName, role, id, cls, name, txt } });\n"
        #     "    } catch(_){}\n"
        #     "    if (out.length >= "
        #     + str(max_nodes)
        #     + ") break;\n"
        #     "  }\n"
        #     "  return out;\n"
        #     "}"
        # )
        # 
        # try:
        #     dom_nodes = await self.page.evaluate(script)
        # except Exception as e:
        #     logger.warning(f"DOM evaluate failed: {e}")
        #     return []
        #
        # results: List[Dict] = []
        # for node in dom_nodes or []:
        #     bbox = node.get("bbox") or [0, 0, 0, 0]
        #     center = node.get("center") or [0, 0]
        #     meta = node.get("meta") or {}
        #     
        #     # 根据location_info选择坐标格式
        #     coords = bbox if location_info == "bbox" else center
        #     
        #     # 构建文本信息
        #     tag = meta.get("tag", "")
        #     role = meta.get("role", "")
        #     eid = meta.get("id", "")
        #     cls = meta.get("cls", "")
        #     name = meta.get("name", "")
        #     txt = meta.get("txt", "")
        #     
        #     text_line = f"tag={tag}; role={role}; id={eid}; class={cls}; name={name}; text={txt}"
        #     results.append({"coordinates": coords, "text": text_line})
        # 
        # logger.info(f"Collected {len(results)} DOM elements")
        # return results


class PCController(BaseController):
    """PC device controller class

    Provides basic operations for Windows/Mac devices.
    """

    def __init__(
        self,
        search_keys: Tuple[str, str] = ("win", "s"),
        ctrl_key: str = "ctrl",
        pc_type: str = "windows",
        max_tokens: int = 1000,
    ):
        """Initialize PC controller

        Args:
            search_keys: Search shortcut keys
            ctrl_key: Control key
            pc_type: Operating system type
            max_tokens: Maximum token count for UI element text, defaults to 1000 tokens
        """
        try:
            self.search_keys = search_keys
            self.ctrl_key = ctrl_key
            self.pc_type = pc_type.lower()
            self.max_tokens = max_tokens
        except Exception as e:
            logger.error(f"Failed to initialize PC controller: {str(e)}")
            raise

    def _take_screenshot(self, filepath: str) -> None:
        """Implement screenshot function for PC device"""
        screenshot = pyautogui.screenshot()
        screenshot.save(filepath)

    def open_app(self, name: str) -> None:
        """Open application

        Args:
            name: Application name
        """
        logger.info(f"Opening application: {name}")
        if self.pc_type in ("linux", "ubuntu"):
            self._open_app_linux(name)
            return

        # Default to Windows behavior
        pyautogui.hotkey(*self.search_keys)
        time.sleep(0.5)

        if self._contains_chinese(name):
            pyperclip.copy(name)
            pyautogui.hotkey(self.ctrl_key, "v")
        else:
            pyautogui.typewrite(name)

        time.sleep(1)
        pyautogui.press("enter")

    def get_screen_xml(self, location_info: str = "center") -> List[Dict]:
        """Get screen element information

        Args:
            location_info: Location information format ('center' or 'bbox')

        Returns:
            List[Dict]: List of element information
        """
        if self.pc_type == "mac":
            logger.warning("Mac OS not supported yet")
            return []
        if self.pc_type in ("linux", "ubuntu"):
            if not _HAS_PYATSPI:
                logger.error("pyatspi is not available; please install 'at-spi2-core' and 'python3-pyatspi'.")
                return []
            t1 = time.time()
            try:
                processor = LinuxElementProcessor(location_info, self.max_tokens)
                elements = processor.collect_elements()
                t2 = time.time()
                logger.info(f"Time taken to get Linux screen element info: {t2 - t1} seconds")
                return elements
            except Exception as e:
                logger.error(f"Linux AT-SPI processing failed: {e}")
                return []
        t1 = time.time()
        try:
            if not _HAS_PYWINAUTO:
                logger.error("pywinauto is not available; Windows UI inspection is unavailable on this platform.")
                return []
            # Get all visible non-taskbar windows
            windows = [w for w in Desktop(backend="uia").windows() if w.is_visible() and w.texts() and w.texts()[0] not in ["任务栏", "Taskbar", ""]]

            if not windows:
                logger.warning("No active window found")
                return []

            active_window = windows[0]  # Get first matching window
            visible_rect = active_window.rectangle()
            t2 = time.time()
            logger.info(f"Time taken to get screen element info: {t2 - t1} seconds")
            processor = WindowsElementProcessor(visible_rect, location_info, self.max_tokens)
            return processor.process_element(active_window)

        except Exception as e:
            logger.error(f"Failed to get screen element info: {str(e)}")
            return []

    def _handle_run(self, action: str) -> None:
        """Handle 'Run' action"""
        code = self._extract_code(action)
        logger.info(f"Executing code: {code}")
        exec(code)

    # -------------------- Linux/Ubuntu helpers --------------------
    def _open_app_linux(self, name: str) -> None:
        """Open application on Linux/Ubuntu.

        Notes:
            - Prefer passing an executable command (e.g., "firefox", "nautilus").
            - If the command is not found, we try "gtk-launch" as a best-effort.
        """
        try:
            cmd_list = shlex.split(name) if name and name.strip() else []
            if not cmd_list:
                logger.error("Empty application name provided for Linux open_app.")
                return

            executable = shutil.which(cmd_list[0])
            if executable:
                subprocess.Popen(cmd_list)
                return

            # Try gtk-launch with desktop id (may succeed for common apps)
            if shutil.which("gtk-launch"):
                try:
                    subprocess.Popen(["gtk-launch", cmd_list[0]])
                    return
                except Exception:
                    pass

            # Fallback: try xdg-open (works for URLs/files; limited for app names)
            if shutil.which("xdg-open"):
                try:
                    subprocess.Popen(["xdg-open", name])
                    return
                except Exception:
                    pass

            logger.error(f"Failed to open '{name}'. Ensure the command exists in PATH or provide a valid desktop id.")
        except Exception as e:
            logger.error(f"Linux open_app failed: {e}")


class LinuxElementProcessor:
    """Linux UI element processor based on AT-SPI (pyatspi).

    Traverse accessible tree and extract visible elements with geometry.
    """

    def __init__(self, location_info: str = "center", max_tokens: int = 1000):
        """Initialize Linux element processor.

        Args:
            location_info: 'center' to return center point, 'bbox' to return bounding box
            max_tokens: maximum token count for element text
        """
        self.location_info = location_info
        self.max_tokens = max_tokens
        self.max_nodes = 3000  # safety cap to avoid excessive traversal
        # Blacklist system UI applications and window managers to avoid desktop components
        self.system_app_blacklist = {
            # Desktop shells
            "gnome-shell",
            "gnome shell",
            # Window managers
            "xfwm4",  # Xfce window manager
            "xfdesktop",  # Xfce desktop manager
            "kwin",
            "kwin_x11",
            "kwin_wayland",  # KDE window manager
            "mutter",  # GNOME 3+ window manager
            "openbox",  # Openbox window manager
            "i3",  # i3 window manager
            "awesome",  # Awesome window manager
            "bspwm",  # bspwm window manager
            "compiz",  # Compiz window manager
            "marco",  # MATE window manager
            "metacity",  # Old GNOME window manager
            "fluxbox",  # Fluxbox window manager
            "enlightenment",  # Enlightenment window manager
            # Desktop panels and system UI
            "xfce4-panel",  # Xfce panel
            "plasma-desktop",  # KDE desktop
            "plasmashell",  # KDE shell
            "lxpanel",  # LXDE panel
            "mate-panel",  # MATE panel
        }
        # Roles that are typically useful for interaction
        self.interactive_roles = {
            # Common UI controls
            "button",
            "push button",
            "toggle button",
            "menu item",
            "menu",
            "combo box",
            "check box",
            "radio button",
            "entry",
            "text",
            "password text",
            "scroll bar",
            "slider",
            "spin button",
            "tab",
            "page tab",
            "toolbar",
            # Web/document elements (important for Firefox)
            "link",
            "hyperlink",
            "heading",
            "paragraph",
            "section",
            "article",
            "document web",
            "document frame",
            "embedded",
            "internal frame",
            # Lists and tables
            "list item",
            "list",
            "tree item",
            "tree",
            "table",
            "table cell",
            "cell",
            "row",
            "column header",
            # Additional interactive elements
            "image",
            "canvas",
            "label",
            "icon",
            "form",
            "panel",
            "layered pane",
        }

    def collect_elements(self) -> List[Dict]:
        """Collect elements from the active (foreground) window only.

        Returns:
            List of dicts with 'coordinates' and 'text'
        """
        elements: List[Dict] = []
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception as e:
            logger.error(f"Failed to get desktop from AT-SPI: {e}")
            return elements

        # Get all available windows
        all_frames = list(self._iter_top_level_frames(desktop))

        # Print debug information: show all available windows
        logger.info("=== Window Debug Information ===")
        logger.info(f"Found {len(all_frames)} top-level windows")
        for idx, frame in enumerate(all_frames, 1):
            try:
                title = self._get_window_title(frame)
                app_name = self._get_application_name(frame)
                role = frame.getRoleName()
                states = self._get_state_set_safe(frame)

                # Collect state information
                state_info = []
                if states is not None:
                    if self._state_contains(states, "STATE_ACTIVE"):
                        state_info.append("ACTIVE")
                    if self._state_contains(states, "STATE_FOCUSED"):
                        state_info.append("FOCUSED")
                    if self._state_contains(states, "STATE_SHOWING"):
                        state_info.append("SHOWING")
                    if self._state_contains(states, "STATE_VISIBLE"):
                        state_info.append("VISIBLE")
                    if self._state_contains(states, "STATE_ICONIFIED"):
                        state_info.append("ICONIFIED")

                is_valid = self._is_valid_root(frame)
                is_valid_fallback = self._is_valid_root_fallback(frame)
                is_system = self._is_system_ui(frame)

                logger.info(f"  [{idx}] Title='{title}' | App='{app_name}' | Role={role}")
                logger.info(
                    f"       State=[{', '.join(state_info) if state_info else 'None'}] | "
                    f"Valid={is_valid} | Fallback={is_valid_fallback} | SystemUI={is_system}"
                )
            except Exception as e:
                logger.warning(f"  [{idx}] Unable to get window information: {e}")
        logger.info("===================")

        # Select active window or fallback to browser/first valid window
        root = None
        active_frame = self._get_active_frame(desktop)

        # Print active window detection results
        if active_frame is not None:
            logger.info(f"Active window detected: '{self._get_window_title(active_frame)}' (App: {self._get_application_name(active_frame)})")
            logger.info(f"  Is active window valid: {self._is_valid_root(active_frame)}")
        else:
            logger.warning("No active window detected, using fallback strategy")

        if active_frame is not None and self._is_valid_root(active_frame):
            root = active_frame
            logger.info(f"✓ Using active window: '{self._get_window_title(root)}'")
        else:
            # First try browser windows, then any valid window
            browser_apps = {"firefox", "chrome", "chromium", "brave", "edge", "safari", "opera"}
            logger.info("Trying to find browser windows...")
            for frame in all_frames:
                if self._is_valid_root_fallback(frame):
                    app_name = self._get_application_name(frame).lower()
                    logger.debug(f"  Checking window: '{self._get_window_title(frame)}' (App: {app_name})")
                    if any(browser in app_name for browser in browser_apps):
                        root = frame
                        logger.info(f"✓ Found browser window: '{self._get_window_title(root)}' (App: {app_name})")
                        break

            if root is None:
                logger.info("No browser window found, using first valid window...")
                for frame in all_frames:
                    if self._is_valid_root_fallback(frame):
                        root = frame
                        logger.info(f"✓ Using fallback window: '{self._get_window_title(root)}' (App: {self._get_application_name(root)})")
                        break

        if root is None:
            logger.error("No valid window found to extract elements from")
            logger.warning("Tip: Some applications require an accessibility client (like Orca) to be running")
            logger.warning("Start Orca with: orca &")
            return elements

        visible_bounds = self._get_extents_bounds(root)
        elements.extend(self._process_accessible(root, visible_bounds, 0))
        logger.info(f"Collected {len(elements)} elements from active window")
        return elements

    # ---------------- Internal helpers ----------------
    def _iter_top_level_frames(self, desktop) -> List[object]:
        """Iterate over all top-level window frames from all applications."""
        for i in range(desktop.childCount):
            app = desktop.getChildAtIndex(i)
            if app is None:
                continue
            for j in range(getattr(app, "childCount", 0)):
                win = app.getChildAtIndex(j)
                try:
                    if win:
                        role = win.getRoleName().lower()
                        if role in ("frame", "window"):
                            yield win
                except Exception:
                    continue

    def _get_active_frame(self, desktop):
        """Get the currently active/focused window frame.

        Tries multiple strategies to find the foreground window:
        1. Follow focus to find parent frame
        2. Find frame with ACTIVE state
        3. Find frame with FOCUSED state

        Returns the first valid match found.
        """
        # Try direct focus API first
        try:
            if hasattr(desktop, "focus"):
                focused = desktop.focus
            elif hasattr(desktop, "get_focus"):
                focused = desktop.get_focus()
            else:
                focused = None

            if focused is not None:
                # Walk up to the top-level frame
                acc = focused
                for _ in range(10):
                    try:
                        role = acc.getRoleName().lower()
                    except Exception:
                        break
                    if role in ("frame", "window"):
                        # Use only if the frame is a valid, visible, non-minimized, non-system UI window
                        if self._is_valid_root(acc):
                            return acc
                        break
                    parent = getattr(acc, "parent", None)
                    if parent is None or parent is acc:
                        break
                    acc = parent
        except Exception:
            pass

        # Fallback 1: find frame with ACTIVE state (most reliable for active window)
        for frame in self._iter_top_level_frames(desktop):
            try:
                states = self._get_state_set_safe(frame)
                if self._state_contains(states, "STATE_ACTIVE") and self._is_valid_root(frame):
                    return frame
            except Exception:
                continue

        # Fallback 2: find frame with FOCUSED state
        for frame in self._iter_top_level_frames(desktop):
            try:
                states = self._get_state_set_safe(frame)
                if self._state_contains(states, "STATE_FOCUSED") and self._is_valid_root(frame):
                    return frame
            except Exception:
                continue

        logger.warning("Could not find active frame")
        return None

    def _get_window_title(self, acc) -> str:
        """Get window title for debugging purposes."""
        try:
            return acc.name or "(untitled)"
        except Exception:
            return "(unknown)"

    # ------ root/window filtering helpers ------
    def _is_valid_root(self, acc) -> bool:
        """Check if accessible is a valid top-level window to traverse.

        Criteria:
        - Role is 'frame' or 'window'
        - Not minimized/iconified and not offscreen
        - Visible/showing
        - Not part of system UI applications (e.g., GNOME Shell)
        """
        try:
            role = (acc.getRoleName() or "").lower()
        except Exception:
            role = ""
        if role not in ("frame", "window"):
            return False

        states = self._get_state_set_safe(acc)

        # If we have state info, check it
        if states is not None:
            # Must not be minimized
            if self._state_contains(states, "STATE_ICONIFIED"):
                return False
            # Prefer showing/visible, but don't strictly require it (some apps don't set these)
            self._state_contains(states, "STATE_SHOWING")
            self._state_contains(states, "STATE_VISIBLE")
            # If neither showing nor visible, it might still be valid if no state info is reliable
            # So we don't filter it out here

        # Exclude system UI windows (e.g., GNOME Shell popups/status menus)
        if self._is_system_ui(acc):
            return False

        return True

    def _is_valid_root_fallback(self, acc) -> bool:
        """Relaxed root validation for fallback selection.

        Criteria:
        - Role is 'frame' or 'window'
        - Not minimized/iconified
        - Not a system UI window
        - Prefer showing/visible if state info is available (but not strictly required)
        """
        try:
            role = (acc.getRoleName() or "").lower()
        except Exception:
            role = ""
        if role not in ("frame", "window"):
            return False

        if self._is_system_ui(acc):
            return False

        states = self._get_state_set_safe(acc)

        if states is not None:
            if self._state_contains(states, "STATE_ICONIFIED"):
                return False
            # If it is showing/visible that's great; otherwise still allow as fallback
        return True

    def _is_system_ui(self, acc) -> bool:
        """Return True if the accessible belongs to a system UI application or window manager.

        This includes desktop shells, window managers, panels, and other desktop environment components.
        """
        app_name = self._get_application_name(acc)
        name_l = (app_name or "").lower().strip()

        # Check against blacklist (exact match or substring)
        for blacklisted in self.system_app_blacklist:
            if blacklisted in name_l or name_l == blacklisted:
                return True

        return False

    def _is_system_application(self, app) -> bool:
        """Return True if given application accessible is a system UI or window manager."""
        try:
            name = (app.name or "").lower().strip()
        except Exception:
            name = ""

        # Check against blacklist (exact match or substring)
        for blacklisted in self.system_app_blacklist:
            if blacklisted in name or name == blacklisted:
                return True

        return False

    def _get_application_name(self, acc) -> str:
        """Best-effort to retrieve application name for an accessible node."""
        # Try direct API
        try:
            app = acc.getApplication()
            if app is not None:
                try:
                    return app.name or ""
                except Exception:
                    pass
        except Exception:
            pass
        # Fallback: walk up to 'application' role
        try:
            ancestor = acc
            for _ in range(20):
                if ancestor is None:
                    break
                try:
                    role = (ancestor.getRoleName() or "").lower()
                except Exception:
                    role = ""
                if role == "application":
                    try:
                        return ancestor.name or ""
                    except Exception:
                        return ""
                ancestor = getattr(ancestor, "parent", None)
        except Exception:
            pass
        return ""

    def _process_accessible(self, acc, visible_bounds, depth: int = 0) -> List[Dict]:
        """Depth-first traversal limited to the active window's visible bounds.

        - Desktop coordinates only; nodes without valid geometry are skipped.
        - Only include elements that are actually SHOWING and VISIBLE (stricter filtering).
        - System UI windows are already filtered at root.
        - For browsers: skip inactive tab pages to avoid element mixing.

        Args:
            acc: Accessible object to process
            visible_bounds: Bounding box of visible area
            depth: Current recursion depth
        """
        results: List[Dict] = []
        if acc is None:
            return results

        # Safety depth/limit
        if depth > 30 or len(results) >= self.max_nodes:
            return results

        states = self._get_state_set_safe(acc)

        # Geometry (desktop coords only)
        rect = self._get_extents_bounds(acc)
        has_valid_rect = False
        coordinates = None
        rect_str = "(unknown)"

        try:
            role = acc.getRoleName()
        except Exception:
            role = "Unknown"
        try:
            name = acc.name or ""
        except Exception:
            name = ""

        if rect is not None:
            left, top, right, bottom = rect
            width = max(0, right - left)
            height = max(0, bottom - top)
            if width > 0 and height > 0 and self._is_within_bounds(rect, visible_bounds):
                has_valid_rect = True
                coordinates = (left + width // 2, top + height // 2) if self.location_info == "center" else (left, top, right, bottom)
                rect_str = f"({left}, {top}, {right}, {bottom})"

        # Inclusion criteria for visible window elements:
        # 1. Must have valid rect within bounds
        # 2. Must not be explicitly offscreen
        # 3. Should be showing/visible if state info is available
        # 4. Must be interactive role OR have meaningful name
        include_node = False
        role_l = (role or "").lower()

        # More balanced state check: filter out obviously invisible elements
        state_ok = False
        if states is None:
            # If no state info available, accept by default (trust geometry check)
            state_ok = True
        else:
            # Check visibility states
            is_showing = self._state_contains(states, "STATE_SHOWING")
            is_visible = self._state_contains(states, "STATE_VISIBLE")
            is_offscreen = self._state_contains(states, "STATE_OFFSCREEN")

            # Element should be showing OR visible (at least one)
            # Only filter out if explicitly offscreen
            if is_offscreen:
                state_ok = False
            elif is_showing or is_visible:
                # Explicitly showing or visible - accept
                state_ok = True
            else:
                # No explicit visibility state - check if it's at least sensitive/enabled
                # This handles cases where Firefox or other apps don't set SHOWING/VISIBLE
                is_enabled = self._state_contains(states, "STATE_ENABLED")
                is_sensitive = self._state_contains(states, "STATE_SENSITIVE")
                is_focusable = self._state_contains(states, "STATE_FOCUSABLE")
                state_ok = is_enabled or is_sensitive or is_focusable

        if has_valid_rect and state_ok and (role_l in self.interactive_roles or bool(name)):
            include_node = True

        if include_node and coordinates is not None:
            truncated = self._truncate_text(name)

            # Skip elements without text unless they are truly interactive controls
            # Layout containers (section, panel) without text are useless for automation
            always_useful_roles = {
                "button",
                "push button",
                "toggle button",
                "link",
                "hyperlink",
                "entry",
                "text",
                "password text",
                "check box",
                "radio button",
                "combo box",
                "list",
                "list item",
                "menu item",
                "menu",
                "tab",
                "page tab",
            }

            # Skip if: no text AND not in always-useful roles
            if not truncated and role_l not in always_useful_roles:
                # Skip layout containers and decorative elements without text
                pass
            else:
                results.append(
                    {
                        "coordinates": coordinates,
                        "text": f"text:{truncated}; control_type:{role}; rect: {rect_str}",
                    }
                )

        # Recurse into children
        try:
            child_count = getattr(acc, "childCount", 0)
            for i in range(child_count):
                child = acc.getChildAtIndex(i)

                # Skip inactive browser tabs/documents to avoid element mixing
                if child is not None and self._should_skip_inactive_tab(child):
                    continue

                results.extend(self._process_accessible(child, visible_bounds, depth + 1))
                if len(results) >= self.max_nodes:
                    break
        except Exception:
            pass

        return results

    def _should_skip_inactive_tab(self, acc) -> bool:
        """Check if this accessible is an inactive browser tab/document that should be skipped.

        In browsers (Firefox, Chrome, etc.), each tab is represented as a document.
        Only the active tab's document should be SHOWING/VISIBLE.
        This prevents mixing elements from multiple tabs.

        Args:
            acc: Accessible object to check

        Returns:
            True if this is an inactive tab that should be skipped
        """
        try:
            role = acc.getRoleName().lower()
        except Exception:
            role = ""

        try:
            getattr(acc, "name", None) or "(unnamed)"
        except Exception:
            pass

        # Check if this is a browser document/tab container
        # Common roles: "document web", "document frame", "page tab", "panel" (in some browsers)
        is_document_container = role in (
            "document web",
            "document frame",
            "page tab",
            "page tab list",  # Tab container in some browsers
            "panel",  # Some browsers use panel for tab content
        )

        if not is_document_container:
            return False

        # For document containers, check if they are actually visible
        states = self._get_state_set_safe(acc)
        if states is None:
            return False

        # Check visibility states
        is_showing = self._state_contains(states, "STATE_SHOWING")
        is_offscreen = self._state_contains(states, "STATE_OFFSCREEN")

        # Skip inactive browser tabs based on role-specific logic
        if role in ("document web", "document frame"):
            # For document containers: only SHOWING documents are active tabs
            should_skip = not is_showing or is_offscreen
        elif role == "panel":
            # Panels are used for various UI elements in browsers
            should_skip = not is_showing or is_offscreen
        elif role in ("page tab", "page tab list"):
            # Tab UI elements - don't skip
            should_skip = False
        else:
            # Other document containers
            is_visible = self._state_contains(states, "STATE_VISIBLE")
            should_skip = is_offscreen or (not is_showing and not is_visible)

        return should_skip

    def _get_extents_bounds(self, acc):
        """Return bounds in desktop coordinates. No Wayland/window fallback."""
        try:
            comp = acc.queryComponent()
            try:
                x, y, w, h = comp.getExtents(pyatspi.DESKTOP_COORDS)
            except TypeError:
                e = comp.getExtents(pyatspi.DESKTOP_COORDS)
                x, y, w, h = int(getattr(e, "x", 0)), int(getattr(e, "y", 0)), int(getattr(e, "width", 0)), int(getattr(e, "height", 0))
            if int(w) <= 0 or int(h) <= 0:
                return None
            return (int(x), int(y), int(x + w), int(y + h))
        except Exception:
            return None

    def _is_within_bounds(self, rect, bounds) -> bool:
        """Check if rect is within bounds.

        Args:
            rect: Element rectangle (left, top, right, bottom)
            bounds: Window bounds (left, top, right, bottom)

        Returns:
            True if rect is within or overlaps with bounds, False otherwise
        """
        if rect is None:
            return False  # No valid rect means not visible
        if bounds is None:
            return True  # No bounds constraint means accept all
        l, t, r, b = rect
        L, T, R, B = bounds
        # Check if rectangles overlap (element is at least partially visible)
        return not (r <= L or l >= R or b <= T or t >= B)

    def _get_state_set_safe(self, acc):
        """Safely get state set from accessible object.

        Args:
            acc: Accessible object

        Returns:
            StateSet object or None if failed
        """
        if acc is None:
            return None
        try:
            # pyatspi uses get_state_set() method (note the underscore)
            if hasattr(acc, "get_state_set"):
                state_set = acc.get_state_set()
                # state_set should be an Atspi.StateSet object
                return state_set
            elif hasattr(acc, "getState"):
                return acc.getState()
            else:
                # No state method available
                return None
        except Exception as e:
            logger.debug(f"Failed to get state set: {e}")
            return None

    def _state_contains(self, state_set, state_name: str) -> bool:
        """Check if state set contains a specific state.

        Args:
            state_set: StateSet object (can be None)
            state_name: State name like 'STATE_ACTIVE'

        Returns:
            True if state is contained, False otherwise
        """
        if state_set is None:
            return False
        try:
            # Get state constant from pyatspi (e.g., pyatspi.STATE_ACTIVE)
            state = getattr(pyatspi, state_name, None)
            if state is None:
                return False
            # StateSet.contains() method checks if state is in the set
            if hasattr(state_set, "contains"):
                return state_set.contains(state)
            else:
                # Fallback: check if state is in the state_set directly
                return state in state_set
        except Exception as e:
            logger.debug(f"Failed to check state {state_name}: {e}")
            return False

    # ---- text truncation for Linux ----
    def _truncate_text(self, text: str) -> str:
        if not text:
            return text
        if self._estimate_token_count(text) <= self.max_tokens:
            return text
        return self._smart_truncate(text)

    def _smart_truncate(self, text: str) -> str:
        tokens = self._tokenize_mixed_text(text)
        result_tokens: List[str] = []
        current = 0
        for token in tokens:
            if token.isspace():
                if current < self.max_tokens:
                    result_tokens.append(token)
                continue
            cost = 0 if token.isspace() else 1
            if current + cost <= self.max_tokens:
                result_tokens.append(token)
                current += cost
            else:
                break
        out = "".join(result_tokens).rstrip()
        return out + "..." if out != text else out

    def _tokenize_mixed_text(self, text: str) -> list:
        import re as _re

        pattern = r"[\u4e00-\u9fff]|[a-zA-Z0-9]+|[^\u4e00-\u9fff\w\s]|\s+"
        return _re.findall(pattern, text)

    def _estimate_token_count(self, text: str) -> int:
        return sum(0 if t.isspace() else 1 for t in self._tokenize_mixed_text(text))


class WindowsElementProcessor:
    """Windows UI element processor class

    Used for analyzing and processing UI elements in Windows windows.
    """

    def __init__(self, visible_rect: RECT, location_info: str = "center", max_tokens: int = 50):
        """Initialize Windows element processor

        Args:
            visible_rect (RECT): Visible area rectangle
            location_info (str): Location information format, can be 'center' or 'bbox'
            max_tokens (int): Maximum token count for text, defaults to 50 tokens
        """
        self.visible_rect = visible_rect
        self.location_info = location_info
        self.max_tokens = max_tokens
        self.SPECIAL_CONTROL_TYPES = {"Hyperlink", "TabItem", "Button", "ComboBox", "ScrollBar", "Edit", "ToolBar"}

    def _contains_chinese(self, text: str) -> bool:
        """Check if text contains Chinese characters

        Args:
            text: Text to check

        Returns:
            bool: Whether text contains Chinese characters
        """
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _truncate_text(self, text: str) -> str:
        """Truncate text based on estimated token count

        Args:
            text (str): Original text

        Returns:
            str: Truncated text with ellipsis if too long
        """
        if not text:
            return text

        # Estimate token count: for English, roughly 1 word = 1 token
        # For Chinese, roughly 1 character = 1 token
        estimated_tokens = self._estimate_token_count(text)

        if estimated_tokens <= self.max_tokens:
            return text

        # Smart truncation for mixed Chinese-English text
        return self._smart_truncate(text)

    def _smart_truncate(self, text: str) -> str:
        """Smart truncation for mixed Chinese-English text

        Args:
            text (str): Input text to truncate

        Returns:
            str: Truncated text with proper handling of mixed content
        """
        # Split text into proper tokens (separate Chinese and English)
        tokens = self._tokenize_mixed_text(text)

        result_tokens = []
        current_token_count = 0

        for token in tokens:
            if token.isspace():
                # Always keep spaces if we haven't exceeded limit
                if current_token_count < self.max_tokens:
                    result_tokens.append(token)
                continue

            # Calculate tokens for this token
            token_count = self._calculate_token_count_for_unit(token)

            if current_token_count + token_count <= self.max_tokens:
                # Can fit the whole token
                result_tokens.append(token)
                current_token_count += token_count
            else:
                # Need to truncate this token
                remaining_tokens = self.max_tokens - current_token_count
                if remaining_tokens > 0:
                    truncated_token = self._truncate_token(token, remaining_tokens)
                    if truncated_token:
                        result_tokens.append(truncated_token)
                break

        result = "".join(result_tokens).rstrip()
        return result + "..." if result != text else result

    def _tokenize_mixed_text(self, text: str) -> list:
        """Tokenize mixed Chinese-English text properly

        Args:
            text (str): Input text to tokenize

        Returns:
            list: List of tokens where Chinese chars and English words are separated
        """
        import re

        # Pattern to match: Chinese characters, English words, or whitespace
        pattern = r"[\u4e00-\u9fff]|[a-zA-Z0-9]+|[^\u4e00-\u9fff\w\s]|\s+"
        tokens = re.findall(pattern, text)
        return tokens

    def _calculate_token_count_for_unit(self, token: str) -> int:
        """Calculate token count for a single unit (should always be 1 after proper tokenization)

        Args:
            token (str): Single token unit

        Returns:
            int: Token count (should be 1 for properly tokenized units)
        """
        if token.isspace():
            return 0  # Spaces don't count as tokens
        return 1  # Each properly tokenized unit counts as 1 token

    def _truncate_token(self, token: str, max_tokens: int) -> str:
        """Truncate a single token

        Args:
            token (str): Token to truncate
            max_tokens (int): Maximum tokens allowed

        Returns:
            str: Truncated token
        """
        if max_tokens <= 0:
            return ""

        if max_tokens >= 1:
            return token  # Single tokens are either kept whole or not at all
        else:
            return ""

    def _estimate_token_count(self, text: str) -> int:
        """Estimate token count for given text

        Args:
            text (str): Input text

        Returns:
            int: Estimated token count
        """
        if not text:
            return 0

        # Use same tokenization logic as smart truncation for consistency
        tokens = self._tokenize_mixed_text(text)

        total_tokens = 0
        for token in tokens:
            total_tokens += self._calculate_token_count_for_unit(token)

        return total_tokens

    def process_element(self, element: UIAWrapper, depth: int = 0) -> List[Dict[str, Union[Tuple[int, ...], str]]]:
        """Process UI element

        Args:
            element (UIAWrapper): UI element to process
            depth (int): Recursion depth, defaults to 0

        Returns:
            List[Dict[str, Union[Tuple[int, ...], str]]]: List of element information, each containing coordinates and text
        """
        current_elements_info = []
        rect = element.rectangle()

        if element.friendly_class_name() == "TitleBar":
            return current_elements_info

        control_type = element.element_info.control_type
        text = element.window_text()

        if rect.width() > 0 and rect.height() > 0 and self._is_element_visible(rect):
            if element.is_enabled():
                coordinates = self._calculate_coordinates(rect)
                rect_str = f"({rect.left}, {rect.top}, {rect.right}, {rect.bottom})"
                # Truncate text to avoid overly long content affecting LLM inference
                truncated_text = self._truncate_text(text)
                current_elements_info.append(
                    {
                        "coordinates": coordinates,
                        "text": f"text:{truncated_text}; control_type:{control_type}; rect: {rect_str}",
                    }
                )

        for child in element.children():
            child_text = child.window_text()
            if not (child.element_info.control_type == "Edit" and child_text and child_text == text):
                current_elements_info.extend(self.process_element(child, depth + 1))

        return current_elements_info

    def _is_element_visible(self, element_rect: RECT) -> bool:
        """Check if element is visible

        Args:
            element_rect (RECT): Element's rectangle area

        Returns:
            bool: Returns True if element is in visible area, False otherwise
        """
        return not (
            element_rect.right < self.visible_rect.left
            or element_rect.left > self.visible_rect.right
            or element_rect.bottom < self.visible_rect.top
            or element_rect.top > self.visible_rect.bottom
        )

    def _calculate_coordinates(self, rect: RECT) -> Union[Tuple[int, int], Tuple[int, int, int, int]]:
        """Calculate element coordinates

        Args:
            rect (RECT): Element's rectangle area

        Returns:
            Union[Tuple[int, int], Tuple[int, int, int, int]]:
                Returns center point coordinates (x, y) if location_info is 'center'
                Returns bounding box coordinates (left, top, right, bottom) if location_info is 'bbox'
        """
        if self.location_info == "center":
            return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        return (rect.left, rect.top, rect.right, rect.bottom)


class ControllerTool:
    """Device control tool class

    Provides unified device control interface, supporting Android and PC devices.
    """

    def __init__(self, platform: str = "Android", **kwargs):
        """Create controller by platform.

        Supported platforms: "Android", "Windows", "Linux", "Ubuntu", "Playwright".
        """
        if platform == "Android":
            self.controller = AndroidController()
        elif platform == "Windows":
            kwargs["pc_type"] = "windows"
            self.controller = PCController(**kwargs)
        elif platform in ("Linux", "Ubuntu"):
            kwargs["pc_type"] = "linux"
            self.controller = PCController(**kwargs)
        elif platform == "Playwright":
            self.controller = PlaywrightController(**kwargs)
        else:
            raise ValueError(f"Unsupported device type: {platform}")

    def __getattr__(self, name):
        """Proxy all method calls to specific controller"""
        return getattr(self.controller, name)


def create_controller(platform: str = "Android", **kwargs) -> ControllerTool:
    """Create controller tool instance

    Args:
        platform: Platform type
        **kwargs: Other parameters

    Returns:
        ControllerTool: Controller tool instance

    Raises:
        ValueError: Raised when device type is invalid
    """
    if platform not in ["Android", "Windows", "Linux", "Ubuntu", "Playwright"]:
        raise ValueError(f"Unsupported device type: {platform}")
    return ControllerTool(platform, **kwargs)
