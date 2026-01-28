# 大语言模型 API 🤖

大语言模型 API 提供了管理 LLM 后端和适配器的功能。通过这些 API，你可以注册、配置和管理不同的大语言模型服务。

## API 端点

### 获取适配器类型

```http
GET/backend-api/api/llm/types
```

获取所有可用的 LLM 适配器类型。

**响应示例：**
```json
{
  "types": [
    "openai",
    "anthropic",
    "azure",
    "local"
  ]
}
```

### 获取所有后端

```http
GET/backend-api/api/llm/backends
```

获取所有已注册的 LLM 后端信息。

**响应示例：**
```json
{
  "data": {
    "backends": [
      {
        "name": "openai",
        "adapter": "openai",
        "config": {
          "api_key": "sk-xxx",
          "api_base": "https://api.openai.com/v1"
        },
        "enable": true,
        "models": ["gpt-4", "gpt-3.5-turbo"]
      }
    ]
  }
}
```

### 获取特定后端

```http
GET/backend-api/api/llm/backends/{backend_name}
```

获取指定后端的详细信息。

**响应示例：**
```json
{
  "data": {
    "name": "anthropic",
    "adapter": "anthropic",
    "config": {
      "api_key": "sk-xxx",
      "api_base": "https://api.anthropic.com"
    },
    "enable": true,
    "models": ["claude-3-opus", "claude-3-sonnet"]
  }
}
```

### 创建后端

```http
POST/backend-api/api/llm/backends
```

注册新的 LLM 后端。

**请求体：**
```json
{
  "name": "anthropic",
  "adapter": "anthropic",
  "config": {
    "api_key": "sk-xxx",
    "api_base": "https://api.anthropic.com"
  },
  "enable": true,
  "models": ["claude-3-opus", "claude-3-sonnet"]
}
```

### 更新后端

```http
PUT/backend-api/api/llm/backends/{backend_name}
```

更新现有后端的配置。

**请求体：**
```json
{
  "name": "anthropic",
  "adapter": "anthropic",
  "config": {
    "api_key": "sk-xxx",
    "api_base": "https://api.anthropic.com",
    "temperature": 0.7
  },
  "enable": true,
  "models": ["claude-3-opus", "claude-3-sonnet"]
}
```

### 删除后端

```http
DELETE/backend-api/api/llm/backends/{backend_name}
```

删除指定的后端。如果后端当前已启用，会先自动卸载。

### 获取适配器配置模式

```http
GET/backend-api/api/llm/types/{adapter_type}/config-schema
```

获取指定适配器类型的配置字段模式。

**响应示例：**
```json
{
  "schema": {
    "title": "OpenAIConfig",
    "type": "object",
    "properties": {
      "api_key": {
        "title": "API Key",
        "type": "string",
        "description": "OpenAI API密钥"
      },
      "api_base": {
        "title": "API Base",
        "type": "string",
        "description": "API基础URL",
        "default": "https://api.openai.com/v1"
      },
      "temperature": {
        "title": "Temperature",
        "type": "number",
        "description": "生成温度",
        "default": 0.7,
        "minimum": 0,
        "maximum": 2
      }
    },
    "required": ["api_key"]
  }
}
```

## 数据模型

### LLMBackendInfo
- `name`: 后端名称
- `adapter`: 适配器类型
- `config`: 配置信息(字典)
- `enable`: 是否启用
- `models`: 支持的模型列表

### LLMBackendList
- `backends`: LLM 后端列表

### LLMBackendResponse
- `error`: 错误信息(可选)
- `data`: 后端信息(可选)

### LLMBackendListResponse
- `error`: 错误信息(可选)
- `data`: 后端列表(可选)

### LLMAdapterTypes
- `types`: 可用的适配器类型列表

### LLMAdapterConfigSchema
- `error`: 错误信息(可选)
- `schema`: JSON Schema 格式的配置字段描述

## 适配器类型

适配器由插件提供，见[适配器实现](../../../llm/adapters)。

目前自带支持的适配器类型包括：

### OpenAI
- 适配器类型: `openai`
- 支持模型: gpt-4, gpt-3.5-turbo 等
- 配置项:
  - `api_key`: API 密钥
  - `api_base`: API 基础 URL
  - `temperature`: 温度参数(可选)

### Anthropic
- 适配器类型: `anthropic`
- 支持模型: claude-3-opus, claude-3-sonnet 等
- 配置项:
  - `api_key`: API 密钥
  - `api_base`: API 基础 URL
  - `temperature`: 温度参数(可选)

### ZhipuAI（智谱清言）
- 适配器类型: `zhipuai`
- 支持模型: glm-4, glm-4-flash, glm-4-plus 等
- 配置项:
  - `api_key`: API 密钥
  - `api_base`: API 基础 URL（默认: `https://open.bigmodel.cn/api/paas/v4`）

### Azure OpenAI
- 适配器类型: `azure`
- 支持 Azure OpenAI 服务部署的各类模型
- 配置项:
  - `api_key`: API 密钥
  - `api_base`: Azure 终结点
  - `deployment_name`: 部署名称

### 本地模型
- 适配器类型: `local`
- 支持本地部署的开源模型
- 配置项:
  - `model_path`: 模型路径
  - `device`: 运行设备(cpu/cuda)

## 相关代码

- [LLM 管理器](../../../llm/llm_manager.py)
- [LLM 注册表](../../../llm/llm_registry.py)
- [适配器实现](../../../llm/adapters)

## 错误处理

所有 API 端点在发生错误时都会返回适当的 HTTP 状态码和错误信息：

```json
{
  "error": "错误描述信息"
}
```

常见状态码：
- 400: 请求参数错误或后端配置无效
- 404: 后端不存在
- 500: 服务器内部错误

## 使用示例

### 获取适配器类型
```python
import requests

response = requests.get(
    'http://localhost:8080/api/llm/types',
    headers={'Authorization': f'Bearer {token}'}
)
```

### 创建新后端
```python
import requests

backend_data = {
    "name": "anthropic",
    "adapter": "anthropic",
    "config": {
        "api_key": "sk-xxx",
        "api_base": "https://api.anthropic.com"
    },
    "enable": true,
    "models": ["claude-3-opus", "claude-3-sonnet"]
}

response = requests.post(
    'http://localhost:8080/api/llm/backends',
    headers={'Authorization': f'Bearer {token}'},
    json=backend_data
)
```

### 更新后端配置
```python
import requests

backend_data = {
    "name": "anthropic",
    "adapter": "anthropic",
    "config": {
        "api_key": "sk-xxx",
        "api_base": "https://api.anthropic.com",
        "temperature": 0.7
    },
    "enable": true,
    "models": ["claude-3-opus", "claude-3-sonnet"]
}

response = requests.put(
    'http://localhost:8080/api/llm/backends/anthropic',
    headers={'Authorization': f'Bearer {token}'},
    json=backend_data
)
```

## 相关文档

- [系统架构](../../README.md#系统架构-)
- [API 认证](../../README.md#api认证-)
- [LLM 适配器开发](../../../llm/README.md#适配器开发-)