# IP 国家归属数据库

本目录不随源码分发本机数据库。请自行取得可合法使用、包含 `country.iso_code` 字段的 MaxMind DB 国家数据库，并保存为 `backend/resources/Country.mmdb`。

例如，可以从 [MaxMind 官方网站](https://www.maxmind.com/)获取 GeoLite2 Country 数据库；下载和使用需遵守提供方条款。

`scripts/geolocation.py` 使用 `maxminddb` 在本机查询，不向第三方提交查询 IP。数据库缺失或无记录时，查询返回未知。启动检查要求 Claude 出口 IP 的国家可以确认，并且与该实例上次成功时的出口国家相同，入口 IP 的位置不参与判断；数据库缺失时，配置代理的实例可能无法通过启动检查。放置数据库后重启管理服务以清除缓存。

`*.mmdb` 已被 Git 忽略，不要提交包含私人信息的其他文件。
