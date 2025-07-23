import traceback
from utils.my_log import logger
from utils.common import Common
import time

from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration

class Blip:
    def __init__(self, data):
        self.common = Common()

        self.config_data = data

        self.processor = BlipProcessor.from_pretrained(self.config_data["model"])
        self.model = BlipForConditionalGeneration.from_pretrained(self.config_data["model"]).to("cuda")

        logger.info("Blip 模型加载完毕")

        #processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-large")
        #model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-large")

        #img_url = 'https://storage.googleapis.com/sfr-vision-language-research/BLIP/demo.jpg' 
        #raw_image = Image.convert('RGB')

        # conditional image captioning
        #text = "a photography of"
        #inputs = processor(raw_image, text, return_tensors="pt")

        #out = model.generate(**inputs)
        #print(processor.decode(out[0], skip_special_tokens=True))

        # unconditional image captioning
        #inputs = processor(img_data, return_tensors="pt")

        #out = model.generate(**inputs)
        #print(processor.decode(out[0], skip_special_tokens=True))


    def generate_caption(self, img_data: str, prompt: str):
        try:
            # 检查 img_data 的类型
            if isinstance(img_data, str):  # 如果是字符串，假定为文件路径
                # 使用 PIL.Image.open() 打开图片文件
                img = Image.open(img_data)
            elif isinstance(img_data, Image.Image):  # 如果已经是 PIL.Image.Image 对象
                # 直接返回这个图像对象
                img = img_data
            else:
                img = img_data

            raw_image = img.convert("RGB")

            inputs = self.processor(raw_image, prompt, return_tensors="pt").to("cuda")

            start_time = time.time()
            out = self.model.generate(**inputs)
            generation_time = time.time() - start_time

            caption = self.processor.decode(out[0], skip_special_tokens=True)
            return caption, generation_time
        except Exception as e:
            logger.error(traceback.format_exc())
            return None, None


    def generate_image_caption(self, img_data, conditional_text=None):
        """
        从给定的图片生成图像描述。
        
        参数:
            img_data (str): 图片的路径。
            conditional_text (str, optional): 条件文本，用于有条件地生成图像描述，默认为None。
            
        返回:
            str: 生成的图像描述。
        """
        # 打开并转换图片
        with Image.open(img_data) as img:
            raw_image = img.convert("RGB")
        
            # 根据是否有条件文本来准备输入
            if conditional_text:
                inputs = self.processor(raw_image, conditional_text, return_tensors="pt").to("cuda")
            else:
                inputs = self.processor(raw_image, return_tensors="pt").to("cuda")
            
            # 生成图像描述
            out = self.model.generate(**inputs)
            
            # 解码输出并返回
            return self.processor.decode(out[0], skip_special_tokens=True)
        return None


    def get_resp_with_img(self, prompt, img_data):
        try:
            caption, time = self.generate_caption(img_data, prompt)
            if caption:
                return caption
            else:
                return None
        except Exception as e:
            logger.error(traceback.format_exc())
            return None
