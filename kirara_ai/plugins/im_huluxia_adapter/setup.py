from setuptools import find_packages, setup

setup(
    name="kirara_ai-huluxia-adapter",
    version="1.0.0",
    description="Huluxia adapter plugin for kirara_ai",
    author="Internal",
    packages=find_packages(),
    install_requires=[
        "aiohttp",
        "pydantic",
    ],
    entry_points={
        "chatgpt_mirai.plugins": ["huluxia = im_huluxia_adapter:HuluxiaAdapterPlugin"]
    },
    include_package_data=True,
    package_data={
        "im_huluxia_adapter": ["assets/*.png"],
    },
)
