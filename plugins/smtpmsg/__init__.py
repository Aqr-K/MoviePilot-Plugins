import base64
import os
import re
import shutil
import socket
import requests
import threading
import urllib.parse

from email.errors import HeaderParseError
from functools import wraps
from pathlib import Path
from typing import Any, List, Dict, Tuple

import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from app.core.config import settings
from app.core.event import eventmanager, Event
# from app.core.plugin import PluginManager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType

lock = threading.Lock()


class SmtpMsgDecorator:
    """
    模块化日志装饰器
    """

    @staticmethod
    def log(mode_name):
        def log_decorator(func):
            @wraps(func)
            def log_wrapper(*args, **kwargs):
                instance_self = args[0] if args else None
                log_more = getattr(instance_self, '_log_more', None) if instance_self else None
                logs = {'msg': "没有日志", 'level': 1}
                try:
                    if log_more:
                        logger.info(f"状态 - {mode_name}模块 - 开始运行")
                    result = func(*args, log_container=logs, **kwargs)
                except Exception as e:
                    logs['msg'] = f"模块运行失败 - 原因 - {e}"
                    logs['level'] = -1
                    raise Exception(logs['msg'])
                finally:
                    level = logs['level']
                    msg = logs['msg']
                    status = None
                    if level == 0:
                        status = "状态"
                    elif level == 1:
                        if log_more:
                            status = "汇报"
                    elif level == 2:
                        status = "警告"
                    elif level == -1:
                        status = "错误"
                    else:
                        status = "未知"
                    if status:
                        logger.info(f"{status} - {mode_name}模块 - {msg}")
                    if (level == 0, 1, 2) and status != ("错误", "未知"):
                        msg = "模块运行成功"
                        status = "状态"
                        logger.info(f"{status} - {mode_name}模块 - {msg}")
                return result

            return log_wrapper

        return log_decorator


class SmtpMsg(_PluginBase):
    # 插件名称
    plugin_name = "SMTP邮件消息通知"
    # 插件描述
    plugin_desc = "支持使用邮件服务器发送消息通知。"
    # 插件图标
    plugin_icon = "Synomail_A.png"
    # 插件版本
    plugin_version = "2.3"
    # 插件作者
    plugin_author = "Aqr-K"
    # 作者主页
    author_url = "https://github.com/Aqr-K"
    # 插件配置项ID前缀
    plugin_config_prefix = "smtpmsg_"
    # 加载顺序
    plugin_order = 29
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled: bool = False
    _send_image: bool = False
    _main: bool = True
    _secondary: bool = False
    _test: bool = False
    _log_more: bool = False
    _other_msgtypes: bool = False
    _enabled_customizable_mail_template: bool = False
    _save: bool = False
    _reset: bool = False
    _enabled_msg_rules: bool = False
    _enabled_customizable_msg_rules: bool = False

    default_template: Path = settings.CONFIG_PATH / ".." / "app" / "plugins" / "smtpmsg" / "template" / "default.html"
    custom_template_dir: Path = settings.PLUGIN_DATA_PATH / "smtpmsg" / "template"
    custom_template: Path = custom_template_dir / "custom.html"
    _test_image: Path = "/public/plugin_icon/Synomail_A.png"

    _main_smtp_host: str = None
    _main_smtp_port: int = None
    _main_smtp_encryption: str = "not_encrypted"
    _main_sender_mail: str = None
    _main_sender_password: str = None
    _secondary_smtp_host: str = None
    _secondary_smtp_port: int = None
    _secondary_smtp_encryption: str = "not_encrypted"
    _secondary_sender_mail: str = None
    _secondary_sender_password: str = None

    _sender_name: str = None
    _receiver_mail: str = None

    # 消息类型
    _msgtypes = []
    _content = ""

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        logger.info(f"汇报 - 初始化插件 - {self.plugin_name}")
        # 读取配置
        if config:
            self._enabled = config.get("enabled", False)
            self._send_image = config.get("enabled_image_send", True)
            self._main = config.get("main", True)
            self._secondary = config.get("secondary", False)
            self._test = config.get("test", False)
            self._log_more = config.get("log_more", False)
            self._other_msgtypes = config.get("other_msgtypes", False)
            self._enabled_customizable_mail_template = config.get("enabled_customizable_mail_template", False)
            self._save = config.get("save", False)
            self._reset = config.get("reset", False)
            self._enabled_msg_rules = config.get("enabled_msg_rules", False)
            self._enabled_customizable_msg_rules = config.get("enabled_customizable_msg_rules", False)
            self._main_smtp_host = config.get("main_smtp_host", )
            self._main_smtp_port = config.get("main_smtp_port", )
            self._main_smtp_encryption = config.get("main_smtp_encryption", "not_encrypted")
            self._main_sender_mail = config.get("main_sender_mail", )
            self._main_sender_password = config.get("main_sender_password", )
            self._secondary_smtp_host = config.get("secondary_smtp_host", )
            self._secondary_smtp_port = config.get("secondary_smtp_port", )
            self._secondary_smtp_encryption = config.get("secondary_smtp_encryption", "not_encrypted")
            self._secondary_sender_mail = config.get("secondary_sender_mail", )
            self._secondary_sender_password = config.get("secondary_sender_password", )
            self._sender_name = config.get("sender_name", )
            self._receiver_mail = config.get("receiver_mail", "")
            self._msgtypes = config.get("msgtypes", [])
            self._content = config.get("content", self.custom_template.read_text(encoding="utf-8"))

        self._check_path()
        self._template_settings()
        self._run_plugin()

    def __update_config(self):
        """
        配置更新
        """
        config = {
            'enabled': self._enabled,
            'enabled_image_send': self._send_image,
            'main': self._main,
            'secondary': self._secondary,
            'test': self._test,
            'log_more': self._log_more,
            'enabled_customizable_mail_template': self._enabled_customizable_mail_template,
            'enabled_msg_rules': self._enabled_msg_rules,
            'enabled_customizable_msg_rules': self._enabled_customizable_msg_rules,
            'content': self.custom_template.read_text(encoding="utf-8"),
            'other_msgtypes': self._other_msgtypes,
            'msgtypes': self._msgtypes,
            'main_smtp_host': self._main_smtp_host,
            'main_smtp_port': self._main_smtp_port,
            'main_smtp_encryption': self._main_smtp_encryption,
            'main_sender_mail': self._main_sender_mail,
            'main_sender_password': self._main_sender_password,
            'secondary_smtp_host': self._secondary_smtp_host,
            'secondary_smtp_port': self._secondary_smtp_port,
            'secondary_smtp_encryption': self._secondary_smtp_encryption,
            'secondary_sender_mail': self._secondary_sender_mail,
            'secondary_sender_password': self._secondary_sender_password,
            'sender_name': self._sender_name,
            'receiver_mail': self._receiver_mail
        }
        self.update_config(config)

    def _check_path(self):
        """
        检查路径与文件
        """
        self.__check_template_file()

    @SmtpMsgDecorator.log("模板文件检查")
    def __check_template_file(self, log_container):
        msg = level = None
        try:
            # 自定义模板不存在，创建模板文件
            if not self.custom_template.exists():
                self.custom_template_dir.mkdir(parents=True, exist_ok=True)
                self.custom_template.touch()
                # 如果_content不为空，写入自定义模板
                if self._content:
                    self.custom_template.write_text(self._content, encoding="utf-8")
                    msg = "自定义邮件模板文件不存在，已创建模板文件，已将数据库内配置写入文件"
                # 否则，复制默认模板到自定义模板
                else:
                    self.default_template.replace(self.custom_template)
                    msg = "自定义邮件模板文件不存在，已创建模板文件，数据库内没有该项配置，还原使用默认配置"

            # 自定义模板存在
            elif self.custom_template.exists():
                # 内容是否一致
                if (self._save is not True
                        and self._reset is not True
                        and self._content != self.custom_template.read_text(encoding="utf-8")):
                    self._content = self.custom_template.read_text(encoding="utf-8")
                    self.__update_config()
                    msg = "自定义邮件模板文件已存在，但与数据库内缓存不一致，提取文件配置并覆盖数据库配置"
                else:
                    msg = '自定义邮件模板文件已存在'
                level = 1
        except Exception as e:
            level = -1
            raise Exception(f'未知错误 - 原因 - {e}')
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("模板配置")
    def _template_settings(self, log_container):
        msg = level = None
        try:
            # 保存自定义模板
            if self._save or self._reset:
                if self._save is True:
                    self.custom_template.write_text(self._content, encoding="utf-8")
                    self._save = False
                    self.__update_config()
                    if self._reset is True:
                        self._reset = False
                        self.__update_config()
                        msg = f"自定义模板与恢复默认模板不可同时启动，关闭恢复默认模板按钮！自定义邮件模板保存成功！"
                    else:
                        msg = "自定义邮件模板保存成功！"
                    self.systemmessage.put(msg)
                elif self._save is not True and self._reset is True:
                    shutil.copy(self.default_template, self.custom_template)
                    self._content = self.custom_template.read_text(encoding="utf-8")
                    self._reset = False
                    self.__update_config()
                    msg = "默认邮件模板恢复成功！"
                if msg:
                    self.systemmessage.put(msg)
                    level = 1
                    return msg
            else:
                level = 1
                msg = "未启用写入自定义模板与回复默认模板，跳过"
        except Exception as e:
            level = -1
            msg = f"模板配置运行失败 - 原因 - {e}"
            raise Exception(msg)

        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    def _run_plugin(self):
        """
        启用插件
        """
        if self._enabled:
            # 参数配置不完整，关闭插件
            if self._main is False and self._secondary is False:
                # 关闭插件
                self._enabled = False
                self._test = False
                self.__update_config()
                msg = "当前参数配置不完整，主服务器与备用服务器至少需要启用一个，关闭插件"
                logger.warning(msg)
                self.systemmessage.put(f"{self.plugin_name}插件{msg}")
                return

            # 测试邮件
            if self._test:
                msg = self.master_program()
                self._test = False
                self.__update_config()
                self.systemmessage.put(msg)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        MsgTypeOptions = []
        for item in NotificationType:
            MsgTypeOptions.append({
                "title": item.value,
                "value": item.name
            })

        # Todo: 消息过滤使用，获取插件列表
        # plugin_manager = PluginManager()
        # local_plugins = plugin_manager.get_local_plugins()
        # PluginTypeOptions = []
        #
        # for index, local_plugin in enumerate(local_plugins, start=1):
        #     PluginTypeOptions.append({
        #         "title": f"{local_plugin.plugin_name}",
        #         "value": local_plugin.id
        #     })

        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'props': {
                            'align': 'center',
                        },
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled_image_send',
                                            'label': '发送图片',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'test',
                                            'label': '发送测试邮件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'log_more',
                                            'label': '记录更多日志',
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VTabs',
                        'props': {
                            'model': '_tabs',
                            'height': 72,
                            'style': {
                                'margin-top': '8px',
                                'margin-bottom': '10px',
                            }
                        },
                        'content': [
                            {
                                'component': 'VTab',
                                'props': {
                                    'value': 'main_smtp',
                                    'style': {
                                        'padding-top': '10px',
                                        'padding-bottom': '10px',
                                        'font-size': '16px'
                                    },
                                },
                                'text': '主SMTP服务器'
                            },
                            {
                                'component': 'VTab',
                                'props': {
                                    'value': 'secondary_smtp',
                                    'style': {
                                        'padding-top': '10px',
                                        'padding-bottom': '10px',
                                        'font-size': '16px'
                                    },
                                },
                                'text': '备用SMTP服务器'
                            },
                            {
                                'component': 'VTab',
                                'props': {
                                    'value': 'email_setting',
                                    'style': {
                                        'padding-top': '10px',
                                        'padding-bottom': '10px',
                                        'font-size': '16px'
                                    },
                                },
                                'text': '邮件设置'
                            },
                            {
                                'component': 'VTab',
                                'props': {
                                    'value': 'custom_template',
                                    'style': {
                                        'padding-top': '10px',
                                        'padding-bottom': '10px',
                                        'font-size': '16px'
                                    },
                                },
                                'text': '自定义邮件模板'
                            },
                            # Todo: 未完成，暂时不显示
                            # {
                            #     'component': 'VTab',
                            #     'disabled': "true",
                            #     'props': {
                            #         'value': 'msg_rules',
                            #         'style': {
                            #             'padding-top': '10px',
                            #             'padding-bottom': '10px',
                            #             'font-size': '16px'
                            #         },
                            #     },
                            #     'text': '消息过滤'
                            # },
                        ]
                    },
                    {
                        'component': 'VWindow',
                        'props': {
                            'model': '_tabs',
                        },
                        'content': [
                            {
                                'component': 'VWindowItem',
                                'props': {
                                    'value': 'main_smtp',
                                    'style': {
                                        'padding-top': '20px',
                                        'padding-bottom': '20px'
                                    },
                                },
                                'content': [
                                    {
                                        'component': 'VForm',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'props': {
                                                    'align': 'center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 3
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'main',
                                                                    'label': '启用主服务器',
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 9
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VAlert',
                                                                'props': {
                                                                    'type': 'info',
                                                                    'variant': 'tonal',
                                                                    'text': '主服务器发送成功时，不使用备用服务器发送消息。（两个服务器至少启用一个）'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'props': {
                                                    'align': 'center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': '12',
                                                            'md': 6
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'main_smtp_host',
                                                                    'label': 'SMTP服务器地址',
                                                                    'placeholder': 'smtp.example.com',
                                                                    'clearable': True
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': '12',
                                                            'md': 4
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'main_smtp_port',
                                                                    'label': 'SMTP服务器端口',
                                                                    'placeholder': '常见：25、465、587、995……',
                                                                    'clearable': True
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': '12',
                                                            'md': 2
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VSelect',
                                                                'props': {
                                                                    'model': 'main_smtp_encryption',
                                                                    'label': '加密方式',
                                                                    'items': [
                                                                        {'title': '不加密', 'value': 'not_encrypted'},
                                                                        {'title': 'SSL', 'value': 'ssl'},
                                                                        {'title': 'TLS', 'value': 'tls'},
                                                                    ],
                                                                }
                                                            }
                                                        ]
                                                    },
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'props': {
                                                    'align': 'center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': '12',
                                                            'md': 6
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'main_sender_mail',
                                                                    'label': 'SMTP邮箱账号',
                                                                    'placeholder': 'example@example.com',
                                                                    'clearable': True
                                                                }
                                                            },
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': '12',
                                                            'md': 6
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'main_sender_password',
                                                                    'label': 'SMTP邮箱密码/Token',
                                                                    'placeholder': 'Passwd or Token',
                                                                    'clearable': True
                                                                }
                                                            },
                                                        ]
                                                    },
                                                ]
                                            },
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VWindowItem',
                                'props': {
                                    'value': 'secondary_smtp',
                                    'style': {
                                        'padding-top': '20px',
                                        'padding-bottom': '20px'
                                    },
                                },
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'secondary',
                                                            'label': '启用备用服务器',
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 9
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'info',
                                                            'variant': 'tonal',
                                                            'text': '主服务器发送失败时，会使用备用服务器发送消息。（两个服务器至少启用一个）'
                                                        }
                                                    }
                                                ]
                                            },
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'secondary_smtp_host',
                                                            'label': '备用SMTP服务器地址',
                                                            'placeholder': 'smtp.example.com',
                                                            'clearable': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'secondary_smtp_port',
                                                            'label': '备用SMTP服务器端口',
                                                            'placeholder': '常见：25、465、587、995……',
                                                            'clearable': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 2
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'secondary_smtp_encryption',
                                                            'label': '加密方式',
                                                            'items': [{'title': '不加密', 'value': 'not_encrypted'},
                                                                      {'title': 'SSL', 'value': 'ssl'},
                                                                      {'title': 'TLS', 'value': 'tls'},
                                                                      ],
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'secondary_sender_mail',
                                                            'label': '备用SMTP邮箱账号',
                                                            'placeholder': 'example@example.com',
                                                            'clearable': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'secondary_sender_password',
                                                            'label': '备用SMTP邮箱密码/Token',
                                                            'placeholder': 'Passwd or Token',
                                                            "clearable": True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                            {
                                'component': 'VWindowItem',
                                'props': {
                                    'value': 'email_setting',
                                    'style': {
                                        'padding-top': '20px',
                                        'padding-bottom': '20px'
                                    },
                                },
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'sender_name',
                                                            'label': '发件人用户名',
                                                            'placeholder': '不输入时，默认使用发件人邮箱作为发件人用户名',
                                                            'clearable': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'receiver_mail',
                                                            'label': '收件人邮箱',
                                                            'placeholder': '默认发送至发件人地址，多个邮箱用英文","分割'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAutocomplete',
                                                        'props': {
                                                            'multiple': True,
                                                            'chips': True,
                                                            'model': 'msgtypes',
                                                            'label': '消息类型',
                                                            'items': MsgTypeOptions,
                                                            'clearable': True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    # {
                                    #     'component': 'VRow',
                                    #     'props': {
                                    #         'align': 'center'
                                    #     },
                                    #     'content': [
                                    #         {
                                    #             'component': 'VCol',
                                    #             'props': {
                                    #                 'cols': 12,
                                    #                 'md': 3
                                    #             },
                                    #             'content': [
                                    #                 {
                                    #                     'component': 'VSwitch',
                                    #                     'props': {
                                    #                         'model': 'other_msgtypes',
                                    #                         'label': '启用第三方消息类型',
                                    #                     }
                                    #                 }
                                    #             ]
                                    #         },
                                    #         {
                                    #             'component': 'VCol',
                                    #             'props': {
                                    #                 'cols': 12,
                                    #                 'md': 9
                                    #             },
                                    #             'content': [
                                    #                 {
                                    #                     'component': 'VAlert',
                                    #                     'props': {
                                    #                         'type': 'info',
                                    #                         'variant': 'tonal',
                                    #                         'text': '启用后，允许发送除官方支持的消息类型以外的其他消息类型通知。（一般用于调试）'
                                    #                     }
                                    #                 }
                                    #             ]
                                    #         },
                                    #     ]
                                    # },
                                ]
                            },
                            {
                                'component': 'VWindowItem',
                                'props': {
                                    'value': 'custom_template',
                                    'style': {
                                        'padding-top': '20px',
                                        'padding-bottom': '20px'
                                    },
                                },
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'enabled_customizable_mail_template',
                                                            'label': '启用自定义模板',
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 9
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'warning',
                                                            'variant': 'tonal',
                                                            'text': '开启"写入自定义模板"后，"恢复默认模板"不会生效！配置在写入后才会生效！'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'save',
                                                            'label': '写入自定义模板',
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'reset',
                                                            'label': '恢复默认模板',
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 6,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'info',
                                                            'variant': 'tonal',
                                                            'text': '重置插件不会重置自定义模板配置，请放心使用！'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAceEditor',
                                                        'props': {
                                                            'modelvalue': 'content',
                                                            'lang': 'html',
                                                            'theme': 'monokai',
                                                            'style': 'height: 20rem; font-size: 14px;',
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'info',
                                                            'variant': 'tonal',
                                                            'text': "支持的变量："
                                                                    "类型：{msg_type}、用户ID：{userid}、标题：{title}、"
                                                                    "内容：{text}、图片：cid:image。"
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VRow',
                                        'props': {
                                            'align': 'center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'info',
                                                            'variant': 'tonal',
                                                            'text': '电脑端可用 "ctrl" + "/" '
                                                                    '快捷键来快速打开/关闭需要注释的内容。'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                            #         {
                            #             'component': 'VWindowItem',
                            #             'props': {
                            #                 'value': 'msg_rules',
                            #                 'style': {
                            #                     'padding-top': '20px',
                            #                     'padding-bottom': '20px'
                            #                 },
                            #             },
                            #             'content': [
                            #                 {
                            #                     'component': 'VRow',
                            #                     'props': {
                            #                         'align': 'center'
                            #                     },
                            #                     'content': [
                            #                         {
                            #                             'component': 'VCol',
                            #                             'props': {
                            #                                 'cols': 12,
                            #                                 'md': 3
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VSwitch',
                            #                                     'props': {
                            #                                         'model': 'enabled_msg_rules',
                            #                                         'label': '启用消息过滤',
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         },
                            #                         {
                            #                             'component': 'VCol',
                            #                             'props': {
                            #                                 'cols': 12,
                            #                                 'md': 3
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VSwitch',
                            #                                     'props': {
                            #                                         'model': 'enabled_customizable_msg_rules',
                            #                                         'label': '启用自定义过滤规则',
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         },
                            #                         {
                            #                             "component": "VCol",
                            #                             "props": {
                            #                                 "cols": 12,
                            #                                 "md": 4
                            #                             },
                            #                             "content": [
                            #                                 {
                            #                                     "component": "VSwitch",
                            #                                     "props": {
                            #                                         "model": "dialog_closed",
                            #                                         "label": "打开自定义过滤规则设置窗口"
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         },
                            #                     ]
                            #                 },
                            #                 {
                            #                     'component': 'VRow',
                            #                     'props': {
                            #                             'align': 'center'
                            #                     },
                            #                     'content': [
                            #                         {
                            #                             'component': 'VCol',
                            #                             'props': {
                            #                                 'cols': 12,
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VAlert',
                            #                                     'props': {
                            #                                         'type': 'info',
                            #                                         'variant': 'tonal',
                            #                                         'text': '该功能为结合已安装插件的插件名，对消息内容进行二次过滤；'
                            #                                                 '不启用自定义过滤规则时，默认屏蔽整个插件的消息。'
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         }
                            #                     ]
                            #                 },
                            #                 {
                            #                     'component': 'VRow',
                            #                     'props': {
                            #                             'align': 'center'
                            #                     },
                            #                     'content': [
                            #                         {
                            #                             'component': 'VCol',
                            #                             'props': {
                            #                                 'cols': 12,
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VAutocomplete',
                            #                                     'props': {
                            #                                         'multiple': True,
                            #                                         'chips': True,
                            #                                         'model': 'allow_plugins',
                            #                                         'label': '需要管理的插件',
                            #                                         'placeholder': '留空，则默认选择所有插件。',
                            #                                         'items': PluginTypeOptions,
                            #                                         "clearable": True,
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         },
                            #                     ]
                            #                 },
                            #                 {
                            #                     'component': 'VRow',
                            #                     'props': {
                            #                             'align': 'center'
                            #                     },
                            #                     'content': [
                            #                         {
                            #                             'component': 'VCol',
                            #                             'props': {
                            #                                 'cols': 12
                            #
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VAutocomplete',
                            #                                     'props': {
                            #                                         'multiple': True,
                            #                                         'chips': True,
                            #                                         'model': 'block_plugins',
                            #                                         'label': '需要排除的插件',
                            #                                         'placeholder': '留空，则默认不过滤任何插件。',
                            #                                         'items': PluginTypeOptions,
                            #                                         'clearable': True,
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         },
                            #                     ]
                            #                 },
                            #                 {
                            #                     'component': 'VRow',
                            #                     'props': {
                            #                             'align': 'center'
                            #                     },
                            #                     'content': [
                            #                         {
                            #                             'component': 'VCol',
                            #                             'props': {
                            #                                 'cols': 12,
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VAlert',
                            #                                     'props': {
                            #                                         'type': 'warning',
                            #                                         'variant': 'tonal',
                            #                                         'text': '目前只支持插件名与邮件主题名一致的插件；'
                            #                                                 '邮件主题 =【插件名】、{title} = '
                            #                                                 '【{local_plugin.plugin_name}】。'
                            #                                     }
                            #                                 }
                            #                             ]
                            #                         }
                            #                     ]
                            #                 },
                            #             ]
                            #         },
                            #     ]
                            # },
                            # {
                            #     "component": "VDialog",
                            #     "props": {
                            #         "model": "dialog_closed",
                            #         "max-width": "65rem",
                            #         "overlay-class": "v-dialog--scrollable v-overlay--scroll-blocked",
                            #         "content-class": "v-card v-card--density-default v-card--variant-elevated rounded-t"
                            #     },
                            #     "content": [
                            #         {
                            #             "component": "VCard",
                            #             "props": {
                            #                 "title": "设置自定义过滤规则"
                            #             },
                            #             "content": [
                            #                 {
                            #                     "component": "VDialogCloseBtn",
                            #                     "props": {
                            #                         "model": "dialog_closed"
                            #                     }
                            #                 },
                            #                 {
                            #                     "component": "VCardText",
                            #                     "props": {},
                            #                     "content": [
                            #                         {
                            #                             'component': 'VRow',
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VCol',
                            #                                     'props': {
                            #                                         'cols': 12,
                            #                                     },
                            #                                     'content': [
                            #                                         {
                            #                                             'component': 'VAceEditor',
                            #                                             'props': {
                            #                                                 'modelvalue': 'site_config',
                            #                                                 'lang': 'json',
                            #                                                 'theme': 'monokai',
                            #                                                 'style': 'height: 30rem',
                            #                                             }
                            #                                         }
                            #                                     ]
                            #                                 }
                            #                             ]
                            #                         },
                            #                         {
                            #                             'component': 'VRow',
                            #                             'props': {
                            #                                     'align': 'center'
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VCol',
                            #                                     'props': {
                            #                                         'cols': 12,
                            #                                     },
                            #                                     'content': [
                            #                                         {
                            #                                             'component': 'VAlert',
                            #                                             'props': {
                            #                                                 'type': 'info',
                            #                                                 'variant': 'tonal'
                            #                                             },
                            #                                             'content': [
                            #                                                 {
                            #                                                     'component': 'span',
                            #                                                     'text': '注意：只有启用高级自定义过滤时，该配置项才会生效，详细配置参考：'
                            #                                                 },
                            #                                                 {
                            #                                                     'component': 'a',
                            #                                                     'props': {
                            #                                                         'href': 'https://github.com/Aqr-K/MoviePilot-Plugins/blob/main/plugins/smtpmsg',
                            #                                                         'target': '_blank'
                            #                                                     },
                            #                                                     'content': [
                            #                                                         {
                            #                                                             'component': 'u',
                            #                                                             'text': 'README'
                            #                                                         }
                            #                                                     ]
                            #                                                 },
                            #                                             ]
                            #                                         },
                            #                                     ]
                            #                                 }
                            #                             ]
                            #                         },
                            #                         {
                            #                             'component': 'VRow',
                            #                             'props': {
                            #                                     'align': 'center'
                            #                             },
                            #                             'content': [
                            #                                 {
                            #                                     'component': 'VCol',
                            #                                     'props': {
                            #                                         'cols': 12,
                            #                                     },
                            #                                     'content': [
                            #                                         {
                            #                                             'component': 'VAlert',
                            #                                             'props': {
                            #                                                 'type': 'info',
                            #                                                 'variant': 'tonal',
                            #                                                 'text': '注意：当"需要管理的插件"中的插件，'
                            #                                                         '在自定义过滤规则未配置内容时，'
                            #                                                         '默认过滤整个插件的消息。'
                            #                                             }
                            #                                         }
                            #                                     ]
                            #                                 }
                            #                             ]
                            #                         },
                            #                     ]
                            #                 }
                            #             ]
                            #         }
                        ]
                    }
                ]
            }
        ], {
            'enabled': False,
            'enabled_image_send': True,
            'main': True,
            'secondary': False,
            'test': False,
            'log_more': False,
            'enabled_customizable_mail_template': False,
            'save': False,
            'reset': False,
            'enabled_msg_rules': False,
            'enabled_customizable_msg_rules': False,
            'content': self.custom_template.read_text(encoding="utf-8"),
            'other_msgtypes': False,
            'msgtypes': [],
            'main_smtp_host': "",
            'main_smtp_port': "",
            'main_smtp_encryption': "not_encrypted",
            'main_sender_mail': "",
            'main_sender_password': "",
            'secondary_smtp_host': "",
            'secondary_smtp_port': "",
            'secondary_smtp_encryption': "not_encrypted",
            'secondary_sender_mail': "",
            'secondary_sender_password': "",
            'sender_name': "",
            'receiver_mail': ""
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        pass

    @eventmanager.register(EventType.NoticeMessage)
    def send(self, event: Event):
        """
        消息发送事件
        """
        if not self.get_state():
            return
        if not event.event_data:
            return
        msg_body = event.event_data
        channel = msg_body.get("channel")
        if channel:
            return
        msg_type: NotificationType = msg_body.get("type")
        title = msg_body.get("title")
        text = msg_body.get("text")
        image = msg_body.get("image")
        userid = msg_body.get("userid")
        if not title and not text:
            logger.warning("标题和内容不能同时为空")
            return
        if (msg_type and self._msgtypes
                and msg_type.name not in self._msgtypes):
            if not self._other_msgtypes:
                logger.info(f"消息类型 {msg_type.value} 未开启消息发送")
                return
        self.master_program(title=title, text=text, msg_type=msg_type, userid=userid, image=image)

    def master_program(self, title=None, text=None, msg_type=None, userid=None, image=None):
        """
        运行主要逻辑
        """
        with lock:
            # todo: 消息过滤，待完善
            # self.__msg_filter(title=title, text=text, msg_type=msg_type, userid=userid)

            m_success = s_success = None
            if self._main:
                smtp_value = 0
                success, server_type = self._determine_server(smtp_value=smtp_value, success=m_success)
                if success:
                    m_success = self._send_to_smtp(smtp_value=smtp_value, server_type=server_type,
                                                   msg_type=msg_type, title=title, text=text, userid=userid,
                                                   image=image)
            if self._secondary:
                smtp_value = 1
                success, server_type = self._determine_server(smtp_value=smtp_value, success=m_success)
                if success:
                    s_success = self._send_to_smtp(smtp_value=smtp_value, server_type=server_type,
                                                   msg_type=msg_type, title=title, text=text, userid=userid,
                                                   image=image)
            # 打印结果
            msg = self._generate_result_log(m_success, s_success)
            return msg

    # Todo：读取json格式配置辅助消息过滤功能的实现
    @SmtpMsgDecorator.log("读取过滤规则")
    def __read_json_filter(self):
        pass

    # Todo：消息过滤功能的实现
    @SmtpMsgDecorator.log("消息过滤")
    def __msg_filter(self, title, text, msg_type, userid):
        pass

    @SmtpMsgDecorator.log("服务器调用判断")
    def _determine_server(self, smtp_value, success, log_container):
        msg = level = status = None
        try:
            if smtp_value == 0:
                server_type = "主"
            elif smtp_value == 1:
                server_type = "备用"
            else:
                raise Exception("未知的SMTP服务器类型")
            if success is None:
                if smtp_value == 0:
                    status = True
            else:
                if self._test:
                    status = self._secondary
                else:
                    if success:
                        status = False
                    else:
                        status = self._secondary
            result = "开始调用" if status else "不需要调用"
            success = status
            msg = f'{server_type}服务器调用判断 - {result}'
            level = 1
            return success, server_type
        except Exception as e:
            level = -1
            msg = f'判断失败 - 原因 - {e}'
            raise Exception(msg)
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("邮件发送")
    def _send_to_smtp(self, smtp_value, log_container, server_type,
                      msg_type=None, title=None, text=None, image=None, userid=None):
        """
        连接-构建-发送 逻辑
        """
        msg = level = server = None
        try:
            if smtp_value == 0:
                smtp_type = "main"
            elif smtp_value == 1:
                smtp_type = "secondary"
            else:
                raise Exception("无效的SMTP服务器类型，无法识别")

            # 消息参数校验
            title, text, image, userid, msg_type = (
                self._msg_parameter_validation(msg_type=msg_type, title=title, text=text, image=image, userid=userid,
                                               server_type=server_type))
            # 读取服务端配置
            self._get_dict_value(server_type=server_type, smtp_type=smtp_type)
            # 连接与认证 SMTP 服务器
            server = self._connect_to_smtp_server()
            # 读取收件人与发件人配置
            receiver_list, sender_name, sender_mail = self._get_receiver_and_sender()
            # 构建邮件
            message = self._msg_build_email(title=title, text=text, image=image, userid=userid, msg_type=msg_type,
                                            sender_name=sender_name, sender_mail=sender_mail)
            # 发送邮件
            send_status = self._send_msg_to_smtp(server=server, message=message, sender_mail=self._sender_mail,
                                                 receiver_list=receiver_list, server_type=server_type)

            msg = "邮件发送成功" if send_status else "邮件发送失败"
            success = True if send_status else False
            level = 1
            return success
        except Exception as e:
            if server:
                server.quit()
            msg = f'出现错误 - {e}'
            success = False
            level = -1
            return success
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("消息参数校验")
    def _msg_parameter_validation(self, log_container, server_type, msg_type=None, title=None, text=None, image=None,
                                  userid=None):
        msg = level = None
        try:
            if self._test:
                msg_type = '测试邮件'
            else:
                if isinstance(msg_type, NotificationType):
                    msg_type = msg_type.value
                else:
                    if msg_type is None:
                        msg_type = ''
                    elif self._other_msgtypes:
                        msg_type = msg_type
                    else:
                        raise Exception("接收到不被支持的消息类型，且未开启第三方消息类型")

            if self._test:
                title = f"测试{server_type}服务器配置"
            else:
                title = title if title is not None else f"【{self.plugin_name}】"

            if self._test:
                text = "这是一封测试邮件~~~"
            else:
                text = text if text is not None else ""

            if self._test:
                userid = "测试用户"
            else:
                userid = userid if userid is not None else ""

            if self._test:
                image = self._test_image
            else:
                image = image if image is not None else ""

            msg = f"消息参数校验成功 - 当前消息类型 - {msg_type}"
            level = 1
            return title, text, image, userid, msg_type

        except Exception as e:
            msg = f'邮件变量校验失败 - 原因 - {e}'
            level = -1
            raise Exception(msg)

        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("连接配置提取")
    def _get_dict_value(self, server_type, smtp_type, log_container):
        """
        获取配置参数
        """
        msg = level = None
        try:
            try:
                _smtp_settings = self.__smtp_settings()
                self._host = _smtp_settings[smtp_type]["host"]
                self._port = _smtp_settings[smtp_type]["port"]
                self._encryption = _smtp_settings[smtp_type]["encryption"]
                self._sender_mail = _smtp_settings[smtp_type]["mail"]
                self._password = _smtp_settings[smtp_type]["password"]
                msg = f"提取{server_type} SMTP 服务端配置成功"
                level = 1

            except KeyError:
                raise Exception(f'{server_type} SMTP 服务端配置参数不完整')
            except Exception as e:
                raise Exception(f'出现异常 - 原因 - {e}')
        except Exception as e:
            level = -1
            raise Exception(e)

        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    def __smtp_settings(self):
        """
        整合配置参数
        """
        try:
            _smtp_settings = {
                "main": {
                    "host": self._main_smtp_host,
                    "port": self._main_smtp_port,
                    "encryption": self._main_smtp_encryption,
                    "mail": self._main_sender_mail,
                    "password": self._main_sender_password,
                },
                "secondary": {
                    "host": self._secondary_smtp_host,
                    "port": self._secondary_smtp_port,
                    "encryption": self._secondary_smtp_encryption,
                    "mail": self._secondary_sender_mail,
                    "password": self._secondary_sender_password,
                }
            }

            return _smtp_settings

        except Exception as e:
            raise Exception(e)

    @SmtpMsgDecorator.log("邮件头参数提取")
    def _get_receiver_and_sender(self, log_container):
        """
        读取收件人与发件人配置
        """
        msg = level = None
        try:
            try:
                if self._receiver_mail:
                    receiver_list = self._receiver_mail.split(",")
                else:
                    receiver_list = self._sender_mail
            except Exception:
                raise Exception('提取收件人配置失败')

            try:
                sender_name = self._sender_name if self._sender_name else self._sender_mail
                sender_mail = self._sender_mail
            except Exception:
                raise Exception('提取发件人配置失败')

            msg = '配置提取成功'
            level = 1
            return receiver_list, sender_name, sender_mail

        except Exception as e:
            msg = f'配置提取失败 - 原因 - {e}'
            level = -1
            raise Exception(msg)

        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("服务器连接")
    def _connect_to_smtp_server(self, log_container):
        msg = level = None
        try:
            try:
                if self._encryption == "ssl":
                    server = smtplib.SMTP_SSL(self._host, self._port, timeout=5)
                else:
                    server = smtplib.SMTP(self._host, self._port, timeout=5)
                    if self._encryption == "tls":
                        server.starttls()

                server.ehlo(self._host)
                server.login(self._sender_mail, self._password)
                msg = "地址连接成功"
                level = 1
                return server
            except socket.timeout:
                raise Exception('建立连接超时')
            except socket.gaierror:
                raise Exception('无法解析主机名或 IP 地址')
            except smtplib.SMTPConnectError:
                raise Exception('无法建立连接')
            except smtplib.SMTPAuthenticationError:
                raise Exception('登录失败，用户名或密码错误')
            except smtplib.SMTPResponseException as e:
                raise Exception(f'返回异常状态码: {e.smtp_code}')
            except smtplib.SMTPServerDisconnected:
                raise Exception('连接已断开')
            except smtplib.SMTPNotSupportedError:
                raise Exception('不支持所需的身份验证方法')
            except (smtplib.SMTPException, Exception):
                raise Exception('登录或者连接时出现未知异常')
        except Exception as e:
            level = -1
            raise Exception(e)

        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    def _msg_build_email(self, title, text, image, userid, msg_type, sender_name, sender_mail, message=None):
        """
        构建邮件
        """
        if not message:
            message = MIMEMultipart()
            msg_html = self.__msg_build_read_email_template(text=text, image=image, title=title, userid=userid,
                                                            msg_type=msg_type)
            message = self.__msg_build_email_Header(message, title, sender_name, sender_mail)
            message = self.__msg_build_email_body(message, image, msg_html)
        if message:
            return message

    @SmtpMsgDecorator.log("模板导入")
    def __msg_build_read_email_template(self, text, image, title, userid, msg_type, log_container):
        msg = level = None
        try:
            try:
                if self._enabled_customizable_mail_template:
                    template = self.custom_template
                else:
                    template = self.default_template
                with open(template, "r", encoding="utf-8") as template_file:
                    template_content = template_file.read()
            except FileNotFoundError:
                raise Exception("没有找到邮件模板文件")
            except PermissionError:
                raise Exception("无法读取邮件模板文件")
            except IsADirectoryError:
                raise Exception("提供了一个目录地址，不是模板文件")
            except UnicodeDecodeError:
                raise Exception("包含非 UTF-8 编码的内容，尝试用 UTF-8 编码读取邮件模板失败")
            except Exception as e:
                raise Exception(f"邮件模板文件读取失败，出现了未知错误 - {e}")
            try:
                msg_html = template_content.format(text=text, image=image, title=title, userid=userid,
                                                   msg_type=msg_type)
            except KeyError:
                raise Exception("邮件模板文件中导入了不被支持的变量")
            except Exception as e:
                raise Exception(f"邮件模板文件在导入变量时遇到了未知错误 - {e}")
            msg = f"成功提取邮件模板并导入变量"
            level = 1
            return msg_html
        except Exception as e:
            msg = e
            level = -1
            raise Exception(e)
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("邮件头构建")
    def __msg_build_email_Header(self, message, title, sender_name, sender_mail, log_container):
        msg = level = None
        try:
            try:
                del message['Subject']
                message['Subject'] = Header(title, "utf-8")
            except HeaderParseError:
                raise Exception('邮件主题包含无效的头部信息或无法解析的内容')
            except UnicodeEncodeError:
                raise Exception('邮件主题包含无法编码为 UTF-8 的字符')
            except TypeError:
                raise Exception('接受到非字符串类型的邮件主题')
            except Exception:
                raise Exception('邮件主题写入构建失败，出现了未知错误')
            try:
                del message['From']
                message['From'] = f'{sender_name} <{sender_mail}>'
            except HeaderParseError:
                raise Exception('发件人用户名包含无效的头部信息或无法解析的内容')
            except UnicodeEncodeError:
                raise Exception('发件人用户名包含无法编码为 UTF-8 的字符')
            except TypeError:
                raise Exception('接受到非字符串类型的发件人用户名')
            except Exception:
                raise Exception('发件人用户名写入失败，出现了未知错误')
            level = 0
            msg = '邮件头构建成功'
            return message
        except Exception as e:
            msg = f'邮件头构建失败 - 原因 - {e}'
            raise Exception(e)
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("邮件体构建")
    def __msg_build_email_body(self, message, image, msg_html, log_container):
        msg = level = None
        try:
            message_alternative = MIMEMultipart('alternative')
            message.attach(message_alternative)
            message = self.___msg_build_email_body_embed_image(image, message)
            html_part = MIMEText(msg_html, 'html', 'utf-8')
            message_alternative.attach(html_part)
            level = 1
            msg = '邮件体构建成功'
            return message
        except Exception as e:
            level = -1
            msg = f'邮件体构建失败 - {e}'
            raise Exception(msg)
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("图片嵌入")
    def ___msg_build_email_body_embed_image(self, image, message, log_container):
        msg = level = image_data = None
        if self._send_image:
            if image:
                try:
                    try:
                        base64_regex = re.compile(r'^data:image/([a-zA-Z]*);base64,(\S*)$', re.IGNORECASE)
                        if isinstance(image, str):
                            parsed_url = urllib.parse.urlparse(image)
                            if parsed_url.scheme in set(urllib.parse.uses_netloc):
                                try:
                                    response = requests.get(image)
                                    if response.status_code == 200:
                                        image_data = response.content
                                    else:
                                        raise Exception(f"获取图片失败。状态码：{response.status_code}")
                                except Exception as e:
                                    raise Exception(f"发生错误 - {e}")
                            elif os.path.isfile(image):
                                try:
                                    with open(image, 'rb') as image_file:
                                        image_data = MIMEImage(image_file.read())
                                except FileNotFoundError:
                                    raise Exception("文件路径不存在")
                                except PermissionError:
                                    raise Exception("没有权限读取图片文件")
                                except IsADirectoryError:
                                    raise Exception("提供了一个目录地址，不是图片文件")
                                except Exception as e:
                                    raise Exception(f"读取图片文件时出错：{e}")
                            elif base64_regex.match(image):
                                try:
                                    base64_data = base64_regex.match(image).group('base64_data')
                                    image_data = base64.b64decode(base64_data)
                                except Exception as e:
                                    raise Exception(f"解码 Base64 数据时出错：{e}")
                            elif isinstance(image, bytes):
                                image_data = image
                            else:
                                raise Exception("无法识别的输入。请提供有效的 URL、本地文件路径或 Base64 编码的数据。")
                        image_data.add_header('Content-ID', '<image>')
                        message.attach(image_data)
                        level = 1
                        msg = '图片文件嵌入成功'
                    except TypeError:
                        raise Exception("接受到非二进制文件，无法将图片文件转码")
                    except Exception as e:
                        raise Exception(e)
                except Exception as e:
                    level = 2
                    msg = f'出现错误，跳过嵌入 - 原因 - {e}'
            else:
                level = 1
                msg = '未传入图片参数，跳过图片嵌入'
        elif not self._send_image:
            level = 1
            msg = '未开启发送图片，抛弃图片数据'
        log_container['msg'] = msg
        log_container['level'] = level
        return message

    @SmtpMsgDecorator.log("邮件发送")
    def _send_msg_to_smtp(self, server, message, sender_mail, receiver_list, server_type, log_container):
        test_type = "测试" if self._test else ""
        msg = level = None
        try:
            try:
                server.sendmail(sender_mail, receiver_list, message.as_string())
            except socket.timeout:
                raise Exception(f"连接超时")
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
                raise Exception("拒绝了接受或发送者地址")
            except smtplib.SMTPDataError:
                raise Exception("拒绝了接受邮件数据，返回了错误响应")
            except (smtplib.SMTPServerDisconnected, ConnectionError):
                raise Exception("断开了连接")
            except smtplib.SMTPAuthenticationError:
                raise Exception("身份验证失败")
            except smtplib.SMTPNotSupportedError:
                raise Exception("不支持某些功能")
            except smtplib.SMTPException:
                raise Exception(f"出现了未知原因")
            msg = f"使用{server_type} SMTP 服务器发送{test_type}邮件成功"
            level = 1
            return True
        except Exception as e:
            msg = f"使用{server_type} SMTP 服务器发送{test_type}邮件失败 - 原因 - {e}"
            level = -1
            raise Exception(msg)
        finally:
            log_container['msg'] = msg
            log_container['level'] = level

    @SmtpMsgDecorator.log("结果汇报")
    def _generate_result_log(self, m_success, s_success, log_container):
        test_type = "测试" if self._test else ""
        msg = level = None
        try:
            if m_success is True:
                if s_success is True:
                    if self._test:
                        msg = f"所有服务器发送{test_type}邮件成功！"
                elif s_success is False:
                    if self._test is True:
                        msg = f"主服务器发送{test_type}邮件成功！备用服务器发送{test_type}邮件失败！"
                elif s_success is None:
                    if self._test is True:
                        msg = f"主服务器发送{test_type}邮件成功！未启动备用服务器！"
                    else:
                        msg = f"主服务器发送{test_type}邮件成功！"
            elif m_success is False:
                if s_success is True:
                    msg = f"备用服务器发送{test_type}邮件成功！主服务器发送{test_type}邮件失败！"
                elif s_success is False:
                    msg = f"所有服务器发送{test_type}邮件失败！"
                elif s_success is None:
                    msg = f"主服务器发送{test_type}邮件失败！备用服务器未启动！无法发送{test_type}邮件！"
            else:
                raise Exception("出现未知错误，无法打印运行结果")
            level = 0
            if self._test:
                return msg
        except Exception as e:
            level = -1
            raise Exception(e)
        finally:
            log_container['msg'] = msg
            log_container['level'] = level