from .common import Common
from .my_log import logger
from .config import Config


class Video:
    def __init__(self, config_path):  
        self.config = Config(config_path)
        self.common = Common()


    # 音频转视频 排队合成
    def wav2video(self, ):
        pass


    
