# 接口统一方案 - get_screen_xml/get_dom 统一

## 修改概览

本次修改统一了不同平台（Android、Windows、Linux、Playwright）的屏幕元素获取接口调用方式。

## 核心变更

### 1. AndroidController 保持不变

**位置**: `appeval/tools/device_controller.py:143-196`

- 保留原有的 `get_screen_xml()` 方法，用于同步获取屏幕元素

### 2. PCController 保持不变

**位置**: `appeval/tools/device_controller.py:897-944`

- 保留原有的 `get_screen_xml()` 方法（支持Windows和Linux），用于同步获取屏幕元素

### 3. PlaywrightController 使用异步 get_screen_xml

**位置**: `appeval/tools/device_controller.py:652-758`

#### 3.1 统一接口名称

```python
# Playwright 也使用 get_screen_xml，但是异步版本
async def get_screen_xml(self, location_info: str = "center", max_nodes: int = 400):
    """Get screen XML/DOM information (async method for Playwright)."""
    # ... DOM 元素收集实现 ...
```

**说明**:
- 所有平台都使用 `get_screen_xml()` 方法名
- Android/Windows/Linux 是同步方法
- Playwright 是异步方法（需要 await）
- 接口名称统一，调用方式因平台而异

### 4. OSAgent 统一调用方式

**位置**: `appeval/roles/osagent.py:512-523`

#### 修改前:
```python
if self.platform in ["Android", "Windows", "Linux"]:
    xml_results = self.controller.get_screen_xml(self.location_info)
    perception_infos.extend(xml_results)
elif self.platform == "Playwright":
    dom_results = await self.controller.async_get_screen_elements(self.location_info)
    perception_infos.extend(dom_results)
```

#### 修改后:
```python
try:
    # All platforms use get_screen_xml (Playwright is async, others are sync)
    if self.platform == "Playwright":
        screen_elements = await self.controller.get_screen_xml(self.location_info)
    else:
        screen_elements = self.controller.get_screen_xml(self.location_info)
    
    logger.debug(screen_elements)
    perception_infos.extend(screen_elements)
except Exception as e:
    logger.warning(f"Screen elements collection failed on {self.platform}: {e}")
```

**改进点**:
- 统一的方法名 `get_screen_xml()`
- 统一的变量名 `screen_elements`
- 单一的错误处理
- 简洁的分支逻辑（只区分是否需要 await）
- 所有平台共享相同的日志和数据处理流程

## 向后兼容性

### ✅ 完全兼容的调用方式

#### 所有平台统一使用 get_screen_xml():

```python
# Android/Windows/Linux - 同步调用
elements = controller.get_screen_xml(location_info="center")

# Playwright - 异步调用（需要 await）
elements = await controller.get_screen_xml(location_info="center")
```

### ⚠️ 注意事项

1. **所有平台都使用 `get_screen_xml()` 方法**:
   - Android/Windows/Linux: 同步方法，直接调用
   - Playwright: 异步方法，需要使用 `await`

2. **Playwright 必须在异步上下文中使用**:
   - ✅ 正确: `await controller.get_screen_xml(location_info)`
   - ❌ 错误: `controller.get_screen_xml(location_info)` （没有 await 会报错）

## 接口命名规范

### 统一的方法名
所有平台都使用 `get_screen_xml()` 方法：
- **Android/Windows/Linux**: `get_screen_xml()` - 同步方法，基于 XML/AT-SPI
- **Playwright**: `async def get_screen_xml()` - 异步方法，基于 DOM API

## 测试建议

### 1. Android 平台测试
```python
from appeval.tools.device_controller import AndroidController

controller = AndroidController()
elements = controller.get_screen_xml("center")
print(f"Found {len(elements)} elements")
```

### 2. Windows/Linux 平台测试
```python
from appeval.tools.device_controller import PCController

controller = PCController(pc_type="windows")
elements = controller.get_screen_xml("center")
print(f"Found {len(elements)} elements")
```

### 3. Playwright 平台测试
```python
import asyncio
from appeval.tools.device_controller import PlaywrightController

async def test_playwright():
    controller = PlaywrightController()
    await controller.initialize()
    await controller.async_goto("https://example.com")
    
    # 获取屏幕元素 - 使用统一的 get_screen_xml 方法（异步）
    elements = await controller.get_screen_xml("center")
    print(f"Found {len(elements)} elements")
    
    await controller.aclose()

asyncio.run(test_playwright())
```

## 总结

这次修改实现了接口完全统一的目标：
- ✅ **所有平台都使用 `get_screen_xml()` 方法名**
- ✅ Android/Windows/Linux 保持同步方法不变
- ✅ Playwright 也使用 `get_screen_xml()`，但是异步版本
- ✅ OSAgent 中调用逻辑极其简洁，只需区分是否 await
- ✅ 代码简洁，接口统一

**设计原则**:
- **统一的方法名**: 所有平台都用 `get_screen_xml()`
- **尊重平台特性**: 同步 vs 异步由平台决定
- **调用方式一致**: 只在需要时添加 `await`
- **简洁明了**: 没有多余的方法和包装器

