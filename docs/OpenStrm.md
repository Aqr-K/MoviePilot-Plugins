# OpenStrm

### 更新记录
- 1.0 更新内容：
  - 插件编写中，未可使用。
  - 增加：
    - 同步刮削入库开关，支持生成strm文件后，立刻刮削整理文件；
    - 网络代理开关，需要提前给服务器配置好代理；
    - CloudFlare IP优选开关，用于处理cf盾问题，需要正确配置CloudFlare IP优选与自定义host；
    - 全局执行周期开关，用于控制没有设置独立定时任务执行周期的站点；
  - 优化：
    - 允许自定义消息汇报的消息类型；
    - 站点独立汇报开关，支持单个站点关闭汇报；
    - Open ANi 支持使用自定义加速站；
    - 支持自动创建不存在的站点保存、刮削保存路径
    - 支持自定义UA，可以用于解决cf盾问题；
    - 支持全局定时任务与站点独立定时任务，支持单个站点设置定时任务。