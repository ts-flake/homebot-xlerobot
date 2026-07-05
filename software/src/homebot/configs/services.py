import logging
from dataclasses import dataclass

from .secrets import get_secrets

logger = logging.getLogger(__name__)


@dataclass
class SpeechConfig:
    """语音引擎配置"""
    # 模型路径
    wakeup_model_path: str = "models/wakeup"
    asr_model_path: str = "models/asr"
    cache_dir: str = "cache"

    # ASR模型文件
    asr_encoder_file: str = "encoder.int8.onnx"
    asr_decoder_file: str = "decoder.onnx"
    asr_joiner_file: str = "joiner.int8.onnx"

    # 唤醒模型文件
    wakeup_encoder_file: str = "encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx"
    wakeup_decoder_file: str = "decoder-epoch-13-avg-2-chunk-16-left-64.onnx"
    wakeup_joiner_file: str = "joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx"
    wakeup_keyword_file: str = "keywords.txt"

    # 音频参数
    sample_rate: int = 16000
    channels: int = 1
    mic_index: int = 1

    # 唤醒词配置
    wakeup_keyword: str = "你好小白"
    wakeup_sensitivity: float = 0.2

    # ASR监听超时（秒）
    listen_timeout: float = 1.5


@dataclass
class TTSConfig:
    """火山引擎TTS配置

    敏感信息（appid, access_token）从 secrets 模块加载
    如需修改，请在 .env.local 文件中设置
    """
    # 以下配置从环境变量/密钥管理加载
    appid: str = ""                           # 应用ID
    access_token: str = ""                    # 访问令牌
    resource_id: str = "seed-tts-2.0"         # 资源ID
    voice_type: str = "zh_female_vv_uranus_bigtts"  # 音色类型
    encoding: str = "pcm"                     # 音频编码
    endpoint: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    sample_rate: int = 16000                  # 输出采样率

    def __post_init__(self):
        """从密钥管理加载敏感配置"""
        if not self.appid or not self.access_token:
            secrets = get_secrets()
            if not self.appid:
                self.appid = secrets.tts.appid
            if not self.access_token:
                self.access_token = secrets.tts.access_token
            # 非敏感配置也可以从环境变量覆盖
            if secrets.tts.resource_id:
                self.resource_id = secrets.tts.resource_id
            if secrets.tts.voice_type:
                self.voice_type = secrets.tts.voice_type


@dataclass
class LLMConfig:
    """LLM API配置

    敏感信息（api_key）从 secrets 模块加载
    如需修改，请在 .env.local 文件中设置
    """
    provider: str = "volcano"                 # 提供商: volcano/deepseek/qwen
    api_key: str = ""                         # API密钥
    api_url: str = "https://ark.cn-beijing.volces.com/api/v3"  # API地址
    model: str = ""                           # 模型名称（火山Ark需要填写模型ID，如 ep-20250324123456-abcdef）
    temperature: float = 0.1                  # 温度参数（低温度=更确定性回复，响应更快）
    max_tokens: int = 256                     # 最大token数（限制回复长度，提升速度）
    top_p: float = 0.9                        # 核采样（控制输出多样性）

    def __post_init__(self):
        """从密钥管理加载敏感配置"""
        secrets = get_secrets()
        if not self.api_key:
            self.api_key = secrets.llm.api_key
        # 非敏感配置可以从环境变量覆盖
        if secrets.llm.api_url:
            self.api_url = secrets.llm.api_url
        if secrets.llm.model:
            self.model = secrets.llm.model
        # 如果没有配置model，给出警告
        if not self.model:
            logger.warning("LLM模型未配置，请在.env.local中设置 ARK_MODEL_ID 或 LLM_MODEL")


@dataclass
class VisionConfig:
    """图片理解/Vision API配置

    支持多提供商: deepseek/qwen/openai
    敏感信息从 secrets 模块加载
    """
    provider: str = "deepseek"                # 提供商
    api_key: str = ""                         # API密钥
    api_url: str = ""                         # API地址
    model: str = ""                           # 模型名称
    temperature: float = 0.7                  # 温度参数
    max_tokens: int = 1024                    # 最大token数

    def __post_init__(self):
        """从密钥管理加载配置"""
        secrets = get_secrets()

        # 如果未指定provider，使用环境变量的配置
        env_provider = secrets.vision.provider
        if env_provider:
            self.provider = env_provider

        # 加载密钥和URL
        if secrets.vision.api_key:
            self.api_key = secrets.vision.api_key
        if secrets.vision.api_url:
            self.api_url = secrets.vision.api_url
        if secrets.vision.model:
            self.model = secrets.vision.model

        # 如果没有单独配置Vision，复用DeepSeek LLM配置
        if self.provider == "deepseek":
            if not self.api_key:
                self.api_key = secrets.llm.api_key
            if not self.api_url:
                self.api_url = secrets.llm.api_url or "https://api.deepseek.com/v1"
            if not self.model:
                self.model = "deepseek-chat"

        # 提供商特定的默认配置
        elif self.provider == "qwen":
            if not self.api_url:
                self.api_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if not self.model:
                self.model = "qwen-vl-plus"

        elif self.provider == "openai":
            if not self.api_url:
                self.api_url = "https://api.openai.com/v1"
            if not self.model:
                self.model = "gpt-4o"
