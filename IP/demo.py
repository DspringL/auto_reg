#! /usr/bin/python
# coding=utf-8
import requests
import time
# 请求地址
targetUrl = "http://myip.ipip.net"
#
# #代理服务器
proxyHost = "代理IP"
proxyPort = 代理端口

#非账号密码验证
proxyMeta = "http://%(host)s:%(port)s" % {

    "host": proxyHost,
    "port": proxyPort,
}
#账号密码验证
#proxyMeta = "http://账号:密码@%(host)s:%(port)s" % {
#    "host": proxyHost,
#    "port": proxyPort,
#}

#
# #pip install -U requests[socks]  socks5代理
#非账号密码验证
# proxyMeta = "socks5://%(host)s:%(port)s" % {
#
#     "host" : proxyHost,
#
#     "port" : proxyPort,
#
# }
#账号密码验证
# proxyMeta = "socks5://账号:密码%(host)s:%(port)s" % {
#
#     "host" : proxyHost,
#
#     "port" : proxyPort,
#
# }
#

proxies = {
    "http": proxyMeta,
    "https": proxyMeta
}
#
start = int(round(time.time() * 1000))
resp = requests.get(targetUrl, proxies=proxies,timeout=10)
costTime = int(round(time.time() * 1000)) - start
print resp.text
print("耗时：" + str(costTime) + "ms")

