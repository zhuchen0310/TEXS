#! /usr/bin/python
# -*- coding:utf-8 -*-

import ujson
import zlib

from ._base import ExchangeBase, WsTypeEnum


class okex(ExchangeBase):

    def __init__(self, loop=None, proxy=None, timeout=5, ws_type=WsTypeEnum.kline.value):
        super().__init__(loop, proxy, timeout, ws_type=ws_type)
        self.exchange_id = 'okex'
        self.http_timeout = 5
        self.ws_timeout = 5
        self.http_data = {
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/72.0.3626.119 Safari/537.36',
                'Content-Type': 'application/json'
            },
            'api': 'https://www.okex.com',
            'urls': {
                'symbols': '/api/spot/v3/instruments',
                'trades': '/api/spot/v3/instruments/{}/trades',
                'klines': '/api/spot/v3/instruments/{}/candles'
            },
            'limits': {
                'kline': 200,
                'trade': 200,
            }
        }
        self.ws_data = {
            'api': {
                'ws_url': 'wss://real.okex.com:10442/ws/v3'
            }
        }
        self.symbols = self.get_symbols()
        self.max_sub_num = 50

    def get_symbols(self):
        api = self.http_data['api']
        path = self.http_data['urls']['symbols']
        url = f'{api}{path}'
        data = self.http.requests_data(url)
        if not data:
            raise BaseException(f'{self.exchange_id} get symbols error')
        symbols = {
            x['instrument_id'].replace('-', '').lower():
                f"{x['instrument_id']}"

            for x in data
        }
        return symbols

    async def get_ws_url(self, ws_type=None):
        """
        功能:
            生成 ws 链接
        """
        return self.ws_data['api']['ws_url']

    async def get_trade_sub_data(self, symbol):
        """
        功能:
            获取 订阅消息
        """
        params = ["spot/trade:{}".format(self.symbols[symbol])]
        return ujson.dumps({
            "op": "subscribe",
            "args": params,
        })

    async def get_kline_sub_data(self, symbol):
        """
        功能:
            获取 订阅消息
        """
        params = ["spot/candle60s:{}".format(self.symbols[symbol])]
        return ujson.dumps({
            "op": "subscribe",
            "args": params,
        })

    async def get_restful_trade_url(self, symbol):
        """
        功能:
            获取 restful 请求的url
        """
        api = self.http_data['api']
        if symbol not in self.symbols:
            raise BaseException(f'{self.exchange_id} {symbol}')
        path = self.http_data['urls']['trades'].format(self.symbols[symbol])
        url = f'{api}{path}?limit=100'
        return url

    async def get_restful_kline_url(self, symbol, timeframe=None, limit=None):
        """
        功能:
            获取 restful 请求的url
        """
        if symbol not in self.symbols:
            raise BaseException(f'{self.exchange_id} {symbol}')
        api = self.http_data['api']
        path = self.http_data['urls']['klines'].format(self.symbols[symbol])
        url = f'{api}{path}?granularity=60'
        return url

    async def parse_restful_trade(self, data, symbol):
        """
        功能:
            处理 restful 返回 trade
            封装成统一格式 保存到Redis中
        返回:
            [[1551760709,"10047738192326012742563","ask",3721.94,0.0235]]
        """
        trade_list = []
        if not data:
            return trade_list
        tick_data_list = data
        for x in tick_data_list:
            format_trade = await self.format_trade([
                await self.str_2_timestamp(x["timestamp"]),
                x['trade_id'],
                x['side'],
                x['price'],
                x['size'],
            ])
            if not format_trade:
                continue
            trade_list.append(format_trade)
        await self.save_trades_to_redis(symbol, trade_list)

    async def parse_trade(self, msg, ws):
        """
        功能:
            处理 ws 实时trade
        """
        try:
            decompress = zlib.decompressobj(
                -zlib.MAX_WBITS  # see above
            )
            inflated = decompress.decompress(msg)
            inflated += decompress.flush()
            data = ujson.loads(inflated)
            if not data:
                return
            if 'data' not in data:
                return
        except Exception as e:
            return
        tick_data_list = data['data']
        symbol = tick_data_list[0]['instrument_id'].replace('-', '').lower()
        trade_list = []
        for x in tick_data_list:
            format_trade = await self.format_trade([
                await self.str_2_timestamp(x["timestamp"]),
                x['trade_id'],
                x['side'],
                x['price'],
                x['size'],
            ])
            if not format_trade:
                continue
            trade_list.append(format_trade)
        await self.save_trades_to_redis(symbol, trade_list)

    async def parse_restful_kline(self, data):
        """
        功能:
            处理 restful 返回 kline
            统一格式 ohlcv = [tms, open, high, low, close, volume]
        """
        ohlcv_list = []
        if not data:
            return ohlcv_list
        for x in data[1:]:  # 第一条是最新的 舍弃
            fmt_kline = await self.format_kline(
                [
                    await self.str_2_timestamp(x[0]),
                    x[1],
                    x[2],
                    x[3],
                    x[4],
                    x[5]
                ]
            )
            if fmt_kline:
                ohlcv_list.append(fmt_kline)
        return ohlcv_list[::-1]

    async def parse_kline(self, msg, ws):
        """
        功能:
            处理 ws kline
        """
        try:
            decompress = zlib.decompressobj(
                -zlib.MAX_WBITS  # see above
            )
            inflated = decompress.decompress(msg)
            inflated += decompress.flush()
            data = ujson.loads(inflated)
            if 'errorCode' in data and data['errorCode'] in [30040, '30040']:
                return data['message']
            if 'data' not in data:
                return
        except Exception as e:
            data = None
        if not data:
            return
        try:
            k_data = data['data'][0]
            kline = k_data['candle']
        except:
            return
        timestamp = await self.str_2_timestamp(kline[0])
        symbol = k_data['instrument_id'].replace('-', '').lower()
        if not symbol:
            return
        ohlcv = await self.format_kline([
            timestamp,
            kline[1],
            kline[2],
            kline[3],
            kline[4],
            kline[5],
        ])
        await self.save_kline_to_redis(symbol, ohlcv)

