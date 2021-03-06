#
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import copy
import statsmodels.api as sm
from sklearn import preprocessing
from datetime import datetime
import shutil
from functools import reduce
from sklearn.covariance import LedoitWolf
from utility.factor_data_preprocess import add_to_panels, align
from utility.tool0 import Data, add_stock_pool_txt
from utility.tool3 import adjust_months, append_df, wr_excel
from utility.relate_to_tushare import stocks_basis, generate_months_ends, trade_days
from utility.single_factor_test import get_firt_industry_list

from utility.stock_pool import financial_condition_pool, factor_condition_pool, concat_stock_pool, save_each_sec_name, \
    del_industry, keep_industry, get_scores, twice_sort, del_market, keep_market, from_stock_wei_2_industry_wei, \
    compute_icir, pattern_filter, index_filter
from utility.download_from_wind import section_stock_infor
from utility.analysis import BackTest, bool_2_ones
from utility.stock_pool import get_scores_with_wei, get_scores, month_return_compare_to_market_index
from utility.select_industry import my_factor_concat, history_factor_return, forecast_factor_return, \
    copy_matrix, forecast_factor_return
from utility.index_enhance import linear_programming, concat_factors_panel, get_factor, get_est_stock_return
from utility.optimization import optimization_fun, optimization_fun_v2
from utility.constant import data_dair, root_dair, default_dict, factor_dict_to_concate  # ,factor_dict_for_scores,
from utility.single_factor_test import get_datdf_in_panel, panel_to_matrix
from utility.template import SelectStockTemplate

import matplotlib

matplotlib.use('Agg')


class FormStockPool(SelectStockTemplate):

    def __init__(self, abs_financial_dict=None, opt_para_dict=None, factor_concate_dict=None,
                 factor_dict_for_scores=None, path_dict=None, use_risk_model=False,
                 alpha_factor=None, risk_factor=None, control_factor=None, select_mode='total_num',
                 twice_sort_dict={}, percent_n=0.2, freq='M', industry_dict=None, special_market_dict=None,
                 benchmark='ZZ500', method='regress', score_mode='all_same', update_ornot='renew',
                 start_date=None, end_date=None, rps_para=85,
                 ):
        """
        :param abs_financial_dict:
        :param factor_wei:
        :param opt_para_dict:
        :param factor_concate_dict:
        :param factor_dict_for_scores:
        :param path_dict:
        :param use_risk_model:
        :param alpha_factor:
        :param risk_factor:
        :param select_mode:
        :param first_max:
        :param twice_sort_dict:
        :param max_n:
        :param freq:
        :param industry_dict:
        :param special_market_dict: 板块设定，如果仅保留创业板的股票或剔除创业板的股票
        :param benchmark:     基准设定
        :param method:
        """

        self._data = Data()
        # 回测开始日期
        if start_date:
            self.start_date = start_date
        else:
            self.start_date = datetime(2009, 3, 1)
        # 回测结束日期
        if end_date:
            self.end_date = end_date
        else:
            self.end_date = datetime.today()
        # 选股方法： 打分法还是回归法
        self.rov_method = method
        # 是否应用风险模型进行权重优化
        self.use_risk_model = use_risk_model

        # 财务绝对要求条件
        self.abs_financial_dict = abs_financial_dict
        # 使用回归法预测股票收益时的所有因子
        if factor_concate_dict:
            self.factor_concate_dict = factor_concate_dict
        else:
            self.factor_concate_dict = factor_dict_to_concate
        # 使用打分法预测股票收益排名时的所有因子
        self.factor_dict_for_scores = factor_dict_for_scores

        # 因子合成地址
        if not path_dict:
            self.path_dict = {'save_path': os.path.join(root_dair, '多因子选股'),
                              'factor_panel_path': os.path.join(root_dair, '因子预处理模块', '因子（已预处理）'),
                              'ic_path': os.path.join(root_dair, '单因子检验', 'ic.csv'),
                              'old_matrix_path': os.path.join(root_dair, '单因子检验', '因子矩阵')}
        else:
            self.path_dict = path_dict

        # alpha因子, self.factor_dict中的key且不再alpha的名称列表里的，是风险因子
        self.alpha_factor = alpha_factor
        if not alpha_factor:
            self.alpha_factor = ['quality', 'growth']
        else:
            self.alpha_factor = alpha_factor

        self.risk_factor = risk_factor
        self.control_factor = control_factor
        self.opt_para_dict = opt_para_dict

        self.factors_panel = None

        # 总股票数量
        self.top_percent_n = percent_n
        # 单个行业的股票数量
        self.one_industry_num = None
        # 因子加权方式为等权
        self.wei_type = 'equal'
        # 测试频率
        self.freq = freq
        # 行业设定
        self.industry_dict = industry_dict
        # 特定板块设定
        self.special_market_dict = special_market_dict
        # 历史的因子表现
        self.factor_ret_history = None
        # 股票的特质收益率
        self.resid = None
        # 计算压缩矩阵时的窗口期
        self.shrinkaged_window = 12
        # 使用打分法时，对不同行业的股票，是否使用同样的大类因子打分，
        # 如果使用同样的大类因子，则为'all_same'，否则为'each_industry'
        self.score_mode = score_mode
        self.icir_window = 6
        # 历史的icir
        self.icir_dict = {}
        # 预期的icir
        self.icir_e_dict = {}
        # 不同截面的组合协方差矩阵
        self.E_dict = None
        # 月度价格变化
        self.pet_chg_m = self._data.PCT_CHG_NM

        self.final_stock_pool = None

        self.rps_para = rps_para
        self.index_wei = None
        self.constituent_industry = None
        self.benchmark = benchmark
        if self.benchmark:
            self.set_benchmark()

        self.banned_stock_list = np.nan

        self.pool = None
        stock_basic = self._data.stock_basic_inform
        self.industry_map = pd.DataFrame({'first_industry': stock_basic['申万一级行业']})

        # 设置为'total_num'，表示从所有股票中选择排名靠前的， 'by_industry'表示分行业选择, twice_sort表示两步排序法
        self.select_type = select_mode
        self.twice_sort_dict = twice_sort_dict
        self.update_or_renew = update_ornot

        self.initial()

    def initial(self):
        self.initial_banned_list()

    def set_indus_wei(self, wei):
        self.index_wei = wei

    # 一些初始化
    def initial_banned_list(self):
        p = r'D:\Database_Stock'
        if os.path.exists(os.path.join(p, 'banned_stock_list.xlsx')):
            banned = pd.read_excel(os.path.join(p, 'banned_stock_list.xlsx'))
            # print('个股基本面较差的股票不能入选，该类个股有:')
            # for i in list(banned['sec_name']):
            #     print(i)
            self.banned_stock_list = list(banned['code'])

    # 设定基准的成分股权重
    def set_benchmark(self):
        if self.benchmark == 'allA':  # 'HS300' 'ZZ500' 'SZ50'
            self.constituent_industry = None
        elif self.benchmark == 'HS300':
            tmp = self._data.hs300_wt
            tmp = tmp / 100
            self.index_wei = tmp
            self.constituent_industry = from_stock_wei_2_industry_wei(tmp)
        elif self.benchmark == 'ZZ500':
            tmp = self._data.zz500_wt
            tmp = tmp / 100
            self.index_wei = tmp
            self.constituent_industry = from_stock_wei_2_industry_wei(tmp)

    def get_stock_pool_by_removing(self):

        # 财务绝对要求条件的股票池构建，如roettm大于5%，eps同比增速大于5，sue位于所有股票的top5%。
        stock_pool = financial_condition_pool(self.abs_financial_dict, self.start_date, self.end_date)

        # 特定行业的股票
        if self.industry_dict:
            if self.industry_dict['handle_type'] == 'delete':
                stock_pool = del_industry(stock_pool, self.industry_dict['to_handle_indus'])
            elif self.industry_dict['handle_type'] == 'keep':
                stock_pool = keep_industry(stock_pool, self.industry_dict['to_handle_indus'])
        # 特定股票板块
        if self.special_market_dict:
            for k, v in self.special_market_dict.items():
                if k == 'delete':
                    stock_pool = del_market(stock_pool, v)
                elif k == 'keep':
                    print('没需求，未实现')
                    stock_pool = keep_market(stock_pool, v)

        # 选择给定时间段的列
        cols = [col for col in stock_pool.columns if col >= self.start_date]
        stock_pool = stock_pool[cols]
        stock_pool.sum()

        # 删除前期为0的几列，如有
        if stock_pool[stock_pool.columns[0]].sum() == 0:
            to_del = []
            for col in stock_pool.columns:
                if stock_pool[col].sum() == 0:
                    to_del.append(col)
                else:
                    break
            stock_pool.drop(to_del, axis=1, inplace=True)

        self.pool = stock_pool
        return stock_pool

    # 规模因子过滤，剔除小于50%分位数以后的小市值股票
    def size_filter(self, top_quantile=0.5):
        negotiablemv = self._data.negotiablemv_daily
        mes = generate_months_ends()
        new_cols = [m for m in mes if m in negotiablemv.columns]
        negotiablemv = negotiablemv[new_cols]

        res_df = pd.DataFrame()
        for col, se in negotiablemv.iteritems():
            mv_tmp = negotiablemv[col]
            mv_tmp = mv_tmp.sort_values(ascending=False)
            mv_tmp = mv_tmp.dropna()
            qua = int(len(mv_tmp.index) * top_quantile)

            res_tmp = pd.DataFrame(False, index=mv_tmp.index, columns=[col])
            res_tmp.loc[mv_tmp.index[:qua], col] = True

            res_df = pd.concat([res_df, res_tmp], axis=1)

        return res_df

    # 对最新一期结果的精准分析
    def precise_analysis(self, stock_value, stock_wei, basic_index_wei):
        # 记录最后一期的各种数据，以进一步分析结果是否合理
        '''
        :param value:       个股alpha因子得分
        :param stock_wei:   个股权重
        :param index_wei:   指数权重
        :return:
        '''

        stock_value, stock_wei, basic_index_wei


    def precise_analysis(self):
        pass


    # 合成新的大类因子。无论是打分法还是回归法，都需要进行该操作。
    def concate_factor(self):
        concate_dict = self.factor_concate_dict
        path_dict = self.path_dict

        # ----------------------------------
        # 因子合成
        if self.update_or_renew == 'renew':
            # 删除过去合成的结果
            if os.path.exists(os.path.join(path_dict['save_path'], '新合成因子')):
                shutil.rmtree(os.path.join(path_dict['save_path'], '新合成因子'))
        print('开始进行因子合成处理.....')

        # 先对所有股票均使用相同的规则合成大类因子
        factors_dict = concate_dict['default']
        my_factor_concat(path_dict, factors_dict, self.update_or_renew, concat_type='equal_weight',
                         start_date=self.start_date)

        # 再对特殊处理的行业按照特定规则合成大类因子
        for ind, factor_dict_tmp in concate_dict.items():
            if ind == 'default':
                continue

            if ind == '计算机':
                print('debug')

            # 针对特殊行业定义路径，使用前期已经处理完毕的行业内因子截面
            tmp_path_dict = {
                'save_path': os.path.join(root_dair, '分行业研究', ind, '多因子选股'),
                'factor_panel_path': os.path.join(root_dair, '分行业研究', ind, '因子（已预处理）'),
            }

            if self.update_or_renew == 'renew':
                if os.path.exists(os.path.join(tmp_path_dict['save_path'], '新合成因子')):
                    shutil.rmtree(os.path.join(tmp_path_dict['save_path'], '新合成因子'))

            # 针对特殊行业进行因子合成
            my_factor_concat(tmp_path_dict, factor_dict_tmp, self.update_or_renew,
                             concat_type='equal_weight', start_date=self.start_date)

            print('{}因子合成完毕！'.format(ind))

    # 因子正交
    def factor_to_symmetry(self, factor_path, save_path, symmetry_dict):
        after_sym_path = os.path.join(save_path, '正交后因子')
        after_sym_panel_spath = os.path.join(after_sym_path, '因子截面')

        if not os.path.exists(after_sym_path):
            os.mkdir(after_sym_path)
        if not os.path.exists(after_sym_panel_spath):
            os.mkdir(after_sym_panel_spath)

        if self.update_or_renew == 'update':
            fd_list = [pd.Timestamp(datetime.strptime(m.split('.')[0], "%Y-%m-%d")) for m in
                       os.listdir(factor_path)]
            hased_list = [pd.Timestamp(datetime.strptime(m.split('.')[0], "%Y-%m-%d")) for m in
                          os.listdir(after_sym_panel_spath)]

            to_compute = [m for m in fd_list if m not in hased_list and m > hased_list[-1]]

            if len(to_compute) == 0:
                print(f"正交后因子数据无需要更新，退出.")
                return 0
            else:
                dirlist = [m.to_pydatetime().strftime("%Y-%m-%d") + '.csv' for m in to_compute]
        else:
            dirlist = os.listdir(factor_path)

        for f in dirlist:
            # f = os.listdir(factors_path)[0]
            # 依次打开每个月度数据
            dat = pd.read_csv(os.path.join(factor_path, f), encoding='gbk',
                              engine='python', index_col=[0])
            for key, values in symmetry_dict.items():
                for v in values:
                    # 正交化处理 线性回归 y=kx  sm.OLS(y, x)
                    model = sm.OLS(dat[v], dat[key], hasconst=None)
                    result = model.fit()
                    tmp_se = pd.Series(index=dat.index, data=result.resid)
                    dat[v] = tmp_se
            dat.to_csv(os.path.join(after_sym_panel_spath, f), encoding='gbk')

    def get_factor_corr(self, factors, path):
        panel_dict = get_datdf_in_panel(path)

        corrs = []
        for date in sorted(panel_dict.keys()):
            factor_panel = panel_dict[date]
            df_tmp = factor_panel[factors]
            corrs.append(df_tmp.corr())

        avg_corr = reduce(lambda df1, df2: df1 + df2, corrs) / len(corrs)
        return avg_corr

    def compute_icir(self, panel_path=None, save_path=None):
        # default的icir值
        if not panel_path:
            panel_path = os.path.join(self.path_dict['save_path'], '新合成因子', '因子截面')
        if not save_path:
            save_path = os.path.join(self.path_dict['save_path'], '新合成因子')

        factors = list(self.factor_concate_dict['default'].keys())
        icir_default = compute_icir(self.icir_window, panel_path, save_path, factors,
                                    update_or_renew=self.update_or_renew)
        if icir_default.empty:
            print('icir计算有bug')
        self.icir_dict.update({'default': icir_default})

        # 再对特殊处理的行业按照特定规则合成大类因子
        for ind, factor_dict in self.factor_concate_dict.items():
            if ind == 'default':
                continue

            print('开始计算{}的icir值'.format(ind))
            panel_path = os.path.join(root_dair, '分行业研究', ind, '多因子选股', '新合成因子', '因子截面')
            save_path = os.path.join(root_dair, '分行业研究', ind, '多因子选股')
            factors = factor_dict.keys()
            icir = compute_icir(self.icir_window, panel_path, save_path, factors,
                                update_or_renew=self.update_or_renew)

            self.icir_dict.update({ind: icir})
        print('ICIR计算完毕')

        # 从历史icir到计算中使用的icir，直接沿着日期向下shift一期。
        self.icir_e_dict = {}
        for key, value in self.icir_dict.items():
            value_shifted = value.shift(1, axis=0)
            value_shifted = value_shifted.dropna(how='all')
            self.icir_e_dict.update({key: value_shifted})

    def compute_factor_return(self):

        factors_dict = self.factor_concate_dict
        path_dict = self.path_dict
        factors, window, half_life = [key for key in factors_dict.keys()], 6, None
        factors.extend(['Pct_chg_nm'])

        fn = list(self.factor_concate_dict['default'].keys())
        fn.append('Pct_chg_nm')
        fn.append('Lncap_barra')
        p = os.path.join(path_dict['save_path'], '正交后因子', '因子截面')
        factors_panel = get_datdf_in_panel(p, fn)
        self.factors_panel = factors_panel
        factor_rets = pd.DataFrame()
        resid = pd.DataFrame()

        if self.update_or_renew == 'renew':
            factor_rets = pd.DataFrame()
            resid = pd.DataFrame()
        elif self.update_or_renew == 'update':
            # 读取已经计算过的因子收益和残差值
            factor_rets = pd.read_csv(os.path.join(root_dair, '临时', 'factor_rets.csv'), encoding='gbk')
            factor_rets.set_index(factor_rets.columns[0], inplace=True)
            factor_rets.index = pd.to_datetime(factor_rets.index)
            resid = pd.read_csv(os.path.join(root_dair, '临时', 'resid.csv'), encoding='gbk')
            resid.set_index(resid.columns[0], inplace=True)
            resid.index = pd.to_datetime(resid.index)
            # 选择出需要计算的月份，并更改factors_panel变量
            to_compute = [i for i in factor_rets.index if i not in list(factors_panel.keys())]
            if len(to_compute) == 0:
                print('因子收益无需更新。')
                return factor_rets, resid
            else:
                to_del = [i for i in list(factors_panel.keys()) if i not in to_compute]
                for d in to_del:
                    factors_panel.pop(d)

        new_factor_ret_history, new_resid = history_factor_return(factors, factors_panel, window, half_life)

        factor_rets = pd.concat([factor_rets, new_factor_ret_history], axis=0)
        resid = pd.concat([resid, new_resid], axis=0)

        # 保存
        factor_rets.to_csv(os.path.join(root_dair, '临时', 'factor_rets.csv'), encoding='gbk')
        resid.to_csv(os.path.join(root_dair, '临时', 'resid.csv'), encoding='gbk')
        # 最后一行的nan先不能删除，因为后面预测时要向后shift一行
        return factor_rets, resid

    def get_shrinkaged(self):
        '''
        E = fX*F_shrinkaged*fX.T + e
        :return:
        '''

        shrinkaged_dict = {}
        self.shrinkaged_window = 12

        # 因子名称
        # fn = [k for k in self.factor_concate_dict['default'].keys()]
        fn = self.risk_factor

        for l in range(self.shrinkaged_window, len(self.factor_ret_history)):

            factor_ret_tmp = self.factor_ret_history.loc[
                self.factor_ret_history.index[l - self.shrinkaged_window:l], fn]
            try:
                cov = LedoitWolf().fit(factor_ret_tmp)
                factor_cov_tmp = cov.covariance_  # 通过压缩矩阵算法得到因子收益协方差矩阵
            except Exception as e:
                print('压缩矩阵1')

            # 该期的因子截面数据
            dat_tmp_df = self.factors_panel[self.factor_ret_history.index[l]]
            f_expo = dat_tmp_df.loc[:, fn].dropna(axis=0, how='any')

            # f_expo为N*M，N为股票数量，M为因子个数； factor_cov_tmp为M*M，f_expo.T 为M*N，最后结果为N*N
            factor_cov = np.dot(np.dot(f_expo.values, factor_cov_tmp), f_expo.values.T)
            factor_cov_df = pd.DataFrame(data=factor_cov, index=f_expo.index, columns=f_expo.index)

            resid_tmp = self.resid.loc[self.factor_ret_history.index[l - self.shrinkaged_window:l], :]
            resid_tmp2 = resid_tmp.loc[:, f_expo.index].fillna(0)

            # 两部分相加，就是整个的股票组合的协方差矩阵，在马科维茨模型中就表示风险
            e_ef = factor_cov_df + resid_tmp2.cov()
            shrinkaged_dict.update({self.factor_ret_history.index[l]: e_ef})

        # wr_excel(r'D:\Database_Stock\临时\e.xlsx', shrinkaged_dict, w_or_r='w')

        return shrinkaged_dict

    def rov_of_all_stocks(self, method):
        # 通过打分法选股, stock_pool 为各个股票的打分
        if method == 'score':
            stock_pool = self.select_stocks_by_scores()
        # 通过回归法选股，stock_pool 为各个股票的预测收益率
        elif method == 'regress':
            stock_pool = self.select_stocks_by_regress()

        return stock_pool

    # 价格选股
    def select_stocks_by_price(self, all_rate=0.1, indus_rate=0.95):
        '''
        :param all_rate:    全部股票内的涨幅排名靠前的比例
        :param indus_rate:  行业内涨跌幅排名靠前的比例
        :return:
        '''
        # 仅通过未经过行业中性、市值中性处理的价格涨跌幅进行选股，
        # 趋势跟踪策略的精髓，价格优先

        price_pool = pd.DataFrame()
        mes = generate_months_ends()
        path = r'D:\Database_Stock\因子预处理模块\因子'

        file_list = os.listdir(path)
        for f in file_list:
            dat = pd.read_csv(os.path.join(path, f), encoding='gbk')
            dat.set_index('Code', inplace=True)
            dat = dat[['Return_12m', 'Return_1m']]
            dat = dat.replace([np.inf, -np.inf], np.nan)
            dat.dropna(how='any', axis=0, inplace=True)

            # 标准化处理
            zscore = preprocessing.StandardScaler()
            dat_array = zscore.fit_transform(dat)
            dat_zs = pd.DataFrame(data=dat_array, index=dat.index, columns=dat.columns)

            val = dat_zs.mean(axis=1)

            val = val.sort_values(ascending=False)
            all_num = int(len(val.index) * all_rate)
            tmp_se = pd.Series(False, index=val.index)
            tmp_se[tmp_se.index[:all_num]] = True

            dd = f.split('.')[0]
            dd = datetime.strptime(dd, "%Y-%m-%d")

            price_pool = pd.concat([price_pool, pd.DataFrame({dd: tmp_se})], axis=1)

        price_pool.columns = pd.to_datetime(price_pool.columns)

        price_pool.sum()

        return price_pool

    def sigal_stock_filter(self, stock_pool, banned_stock_list):
        if not isinstance(banned_stock_list, list):
            return stock_pool
        else:
            # 对每个个股，如果不是nan，则设置成最小的值
            min_value = stock_pool.min().min()
            for stock in banned_stock_list:
                if stock in stock_pool.index:
                    for col in stock_pool.columns:
                        if not np.isnan(stock_pool.loc[stock, col]):
                            stock_pool.loc[stock, col] = min_value

            return stock_pool

    def fund_filter(self, num=500, year=2017):
        fund_holding_mkv = self._data.FUND_HOLDING_MKV
        basic_inform = self._data.stock_basic_inform

        res_df = pd.DataFrame()
        for column in fund_holding_mkv.columns:
            tmp = fund_holding_mkv[column].sort_values(ascending=False)
            if (tmp != 0).sum() <= num:
                tmp.where(tmp != 0, False, inplace=True)
                tmp.where(tmp == False, True, inplace=True)
            else:
                tmp[tmp.index[:num]] = True
                tmp.where(tmp == True, False, inplace=True)

            tmp_df = pd.DataFrame({column: tmp})
            res_df = pd.concat([res_df, tmp_df], axis=1)

        test = res_df.sum()

        # 补全股票代码
        added_stock = [stock for stock in basic_inform.index if stock not in list(res_df.index)]
        if len(added_stock) > 0:
            added_df = pd.DataFrame(False, index=added_stock, columns=res_df.columns)
            res_df = pd.concat([res_df, added_df], axis=0)

        # year以前年份的全为True，因为以前的市场结构不一样
        all_true_cols = [col for col in res_df.columns if col.year < year]
        res_df[all_true_cols] = True

        return res_df

    def select_stocks_by_regress(self):

        # 预测的因子收益
        est_facor_rets = forecast_factor_return(self.alpha_factor, self.factor_ret_history, window=12)
        # 全部股票收益预测
        est_stock_rets = get_est_stock_return(self.alpha_factor, self.factors_panel, est_facor_rets, 12, 6)
        print('计算预期收益率完成...')

        est_stock_rets.name = 'stock_return'

        if isinstance(self.pool, pd.DataFrame):
            # 使用财务数据过滤一下股票池
            self.pool
            ret = est_stock_rets
        else:
            ret = est_stock_rets

        return est_stock_rets

    def select_stocks_by_scores(self):
        new_stock_pool = pd.DataFrame()

        if self.update_or_renew == 'update_111':
            p = r'D:\Database_Stock\临时\stock_pool.csv'
            if os.path.exists(p):
                new_stock_pool = pd.read_csv(p, encoding='gbk')
                new_stock_pool.set_index(new_stock_pool.columns[0], inplace=True)
                new_stock_pool.columns = pd.to_datetime(new_stock_pool.columns)

            cols = [col for col in self.pool.columns if col not in new_stock_pool.columns and
                    col > new_stock_pool.columns[-1]]
            # 无需要更新的，返回
            if len(cols) == 0:
                return new_stock_pool
            else:
                # 若有需要更新的，则选出指定的列
                pool = self.pool[cols]
        else:
            new_stock_pool = pd.DataFrame()
            pool = self.pool

        for col, value in pool.iteritems():
            codes = list(value.index)

            # 对所有股票使用同样的打分大类因子
            all_codes_scores = self.scores_for_stocks(codes, col)
            # 对不同行业的股票使用不同的打分大类因子
            if isinstance(all_codes_scores, pd.DataFrame):
                scores = self.scores_for_special_industry(all_codes_scores, codes, col)
            else:
                scores = None

            if isinstance(scores, pd.DataFrame):
                new_stock_pool = pd.concat([new_stock_pool, scores], axis=1)
            elif not scores:
                continue

        new_stock_pool = new_stock_pool.sort_index()

        p = r'D:\Database_Stock\临时\stock_pool.csv'
        new_stock_pool.to_csv(p, encoding='gbk')

        return new_stock_pool

    # 在打分法模型中，对所有股票使用同样的大类因子。该种做法，主要用在需要保证收益预测模型可控的时候，
    # 测算一些其他功能时使用，比如测算风险模型时、测算对冲模型时。
    def scores_for_stocks(self, codes, dt):
        scores = pd.Series()
        tmp_root = os.path.join(root_dair, '多因子选股', '新合成因子', '因子截面')
        f_list = os.listdir(tmp_root)
        fn = dt.strftime("%Y-%m-%d") + '.csv'
        if fn not in f_list:
            print('未找到' + fn + '该期数据')
            return None

        data = pd.read_csv(os.path.join(tmp_root, fn), engine='python', encoding='gbk')
        data = data.set_index('Code')
        new_index = [c for c in codes if c in data.index]
        data = data.loc[new_index, :]
        fn = self.factor_dict_for_scores['default']

        f_list = [f for f in fn if f in data.columns]
        if len(f_list) == 0:
            return None
        icir_dat = self.icir_e_dict['default']
        if dt not in list(icir_dat.index):
            print('该日期还未计算出icir')
            return None
        icir_se = icir_dat.loc[dt, :]
        if self.wei_type == 'equal':
            wei = icir_se[f_list] / abs(icir_se[f_list])
            wei = np.array(wei / np.nansum(np.abs(wei)))
        elif self.wei_type == 'icir':
            wei = icir_se[f_list]
            wei = np.array(wei / np.nansum(wei))

        val = data[f_list].values
        wei = np.array(wei / np.nansum(wei))
        wei = abs(wei)
        scores1 = pd.Series(index=data.index, data=np.dot(val, wei))
        scores = pd.concat([scores, scores1])

        if len(scores) == 0:
            return None
        else:
            return pd.DataFrame({dt: scores})

    # 在打分法模型中，对不同的行业使用不同的大类因子，在做单行业测试，或者行业内新因子测算的使用用到。月报里也使用该模型。
    def scores_for_special_industry(self, original_scores, codes, dt):

        score_tmp = copy.deepcopy(original_scores)
        for ind, factor_dict in self.factor_dict_for_scores.items():
            if ind == 'default':
                continue

            print(ind)

            if ind == '食品饮料' and dt.year==2020 and dt.month==12:
                print('debug')

            tmp_root = os.path.join(root_dair, '分行业研究', ind, '多因子选股', '新合成因子', '因子截面')
            f_list = os.listdir(tmp_root)
            fn = dt.strftime("%Y-%m-%d") + '.csv'

            if fn not in f_list:
                print('在{}行业上，未找到{}期数据'.format(ind, fn))
                continue
            data = pd.read_csv(os.path.join(tmp_root, fn), engine='python', encoding='gbk')
            data = data.set_index('Code')
            new_index = [c for c in codes if c in data.index]
            data = data.loc[new_index, :]

            factor_ns = self.factor_dict_for_scores[ind]

            f_list = [f for f in factor_ns if f in data.columns]
            # 分行业选择，如果行业没在key里面，则使用default。
            if len(f_list) == 0:
                continue

            icir_dat = self.icir_e_dict[ind]
            if dt not in list(icir_dat.index):
                continue
            icir_se = icir_dat.loc[dt, :]
            icir_se = icir_se.dropna()

            # 非负项调整
            non_neg_fac_names = ['margin', 'quality', 'growth', 'west', 'rd', 'fund_hold', 'size']
            to_non_neg = []
            for i, v in icir_se.items():
                if v < 0 and i in non_neg_fac_names:
                    to_non_neg.append(i)
            icir_se = icir_se.drop(to_non_neg)
            # 都有的factor
            all_f = [i for i in icir_se.index if i in data.columns]

            # sco = get_scores_with_wei(data, f_list, icir_se)
            val = data[all_f].values

            wei = icir_se[all_f] / abs(icir_se[all_f])
            wei = np.array(wei / np.nansum(np.abs(wei)))
            # wei = np.array(icir_se[f_list] / np.nansum(icir_se[f_list]))
            scores_tmp = pd.Series(index=data.index, data=np.dot(val, wei))

            # 如果设定了每个行业仅选择N个公司
            if self.one_industry_num:
                scores_sorted = scores_tmp.sort_values(ascending=False)
                tmp = list(scores_sorted.index[:self.one_industry_num])
                scores_sorted = scores_sorted[tmp]
                return scores_sorted

            codes_in_origi = [i for i in scores_tmp.index if i in score_tmp.index]
            score_tmp.loc[codes_in_origi, score_tmp.columns[0]] = scores_tmp[codes_in_origi]

        return score_tmp

    # 根据股票池得分，选择得分排名前N的股票
    def select_top_n(self, pool, top_percent=None, by_industry=True):
        '''

        :param pool:
        :param top_percent:   选股比例
        :param by_industry:   是否分行业选择
        :return:  返回的是权重数据，入选股票池的股票权重非0，权重为0的表明为入选股票池
        '''

        if not top_percent:
            top_percent = self.top_percent_n

        if by_industry:
            basic_inform = self._data.stock_basic_inform
            basic_inform = basic_inform['申万一级行业']

        res_df = pd.DataFrame()
        for col, se in pool.iteritems():

            # 分行业排序，每个行业选择排序前top_percent的公司
            if by_industry:
                sel = []
                tmp = pd.concat([basic_inform, se], axis=1)
                tmp = tmp.dropna()
                grouped = tmp.groupby(tmp.columns[0])
                for k, g in grouped:

                    if col.year == 2020 and col.month == 10 and k == '食品饮料':
                        # print('达到指定日期的指定行业')
                        gs = g.sort_values(g.columns[1], ascending=False)

                    num = int(len(g) * top_percent)
                    g_sorted = g.sort_values(g.columns[1], ascending=False)
                    selected = list(g_sorted.index[:num])
                    sel.extend(selected)

                sco = pd.Series(index=sel, data=np.ones(len(sel)))
                sco = sco / sco.sum()
                res_df = pd.concat([res_df, pd.DataFrame({col: sco})], axis=1)

            # 全部股票排序，选择排序前top_percent的公司，当不同行业使用不同的alpha因子时会有有些问题
            else:
                se = se.dropna()
                tmp_sorted = se.sort_values(ascending=False)
                man_n = int(len(tmp_sorted) * top_percent)
                if man_n < 5:
                    man_n = 5
                if man_n > 50:
                    man_n = 50
                sel = list(tmp_sorted[:man_n].index)
                sco = pd.Series(index=sel, data=np.ones(len(sel)))
                sco = sco / sco.sum()
                res_df = pd.concat([res_df, pd.DataFrame({col: sco})], axis=1)

        res_df = res_df.fillna(0)

        # 看看最后一期选的股票是什么
        tmp_se = copy.deepcopy(res_df[res_df.columns[-1]])
        tmp_se.replace(0.0, np.nan, inplace=True)
        tmp_se.dropna(inplace=True)
        codes = list(tmp_se.index)
        basic = self._data.stock_basic_inform
        names = basic.loc[codes, 'SEC_NAME']
        len(names)
        return res_df

    # 通过rps指标选择涨的好的标的
    def rps_select(self, stock_pool, fre='w', rps_para=np.nan):
        # 回测时的频率可用周频，跟踪时的频率可用日频。
        rps_value = self._data.RPS
        days_w = trade_days(freq=fre, interface='tushare')
        new_columns = [c for c in rps_value.columns if c in days_w]
        rps_w = rps_value[new_columns]

        if np.isnan(rps_para):
            rps_para = self.rps_para
        rps_w[rps_w < rps_para] = np.nan

        stock_pool = append_df(stock_pool, target_feq=fre)

        rps_w, stock_pool = align(rps_w, stock_pool)
        stock_pool = stock_pool.replace(0.0, np.nan)

        res_df = pd.DataFrame()
        for col in stock_pool.columns:
            if col == stock_pool.columns[-1]:
                print('hhh')

            rps_tmp = rps_w[[col]]
            pool_tmp = stock_pool[[col]]

            rps_tmp.columns = ['rps']
            pool_tmp.columns = ['alpha']

            tmp_df = pd.concat([rps_tmp, pool_tmp], axis=1)
            tmp_df = tmp_df.dropna(how='any', axis=0)

            if len(tmp_df.index) > 0:
                tt = np.ones([len(tmp_df.index), 1]) / len(tmp_df.index)
                pool_tt = pd.DataFrame(index=tmp_df.index, columns=[col], data=tt)
            else:
                pool_tt = pd.DataFrame(columns=[col])

            res_df = pd.concat([res_df, pool_tt], axis=1)

        stock_num = pd.Series()
        # 看看每期的股票数量
        for col, se in res_df.iteritems():
            stock_num = stock_num.append(pd.Series({col: len(se.dropna())}))

        return res_df

    # 风险模型（组合优化）
    def compose_optimization(self, stock_pool, is_enhance=True, lamda=10, turnover=None, te=None,
                             industry_expose_control=True, industry_max_expose=0,
                             in_benchmark=False, in_benchmark_wei=0.8,
                             limit_factor_panel=None, max_num=None, s_type=None):
        '''
        :param stock_pool:    每期的alpha排序或预测的股票收益
        :param is_enhance:    是否是指数增强
        :param lamda:         风险厌恶系数
        :param turnover:      是否有换手率约束
        :param te:            是否有跟踪误差约束
        :param industry_max_expose  行业风险敞口，如果该值为0，则表示行业中性
        :param size_neutral   是否做市值中性
        :param in_benchmark:  是否必须成份股内选股
        :param max_num:       最大股票数量要求
        :return: stock_wei:   每期的股票权重
        '''

        his_tamp = list(set(stock_pool.columns) & set(self.E_dict.keys()))
        his_tamp.sort()
        wei_df = pd.DataFrame()
        save_path = os.path.join(root_dair, '临时')
        pre_w = None
        # todo 添加一个读取已经优化好的权重数据的功能。
        for d in his_tamp:
            # if d < datetime(2019, 4, 27):
            #             #     continue
            if d == datetime(2014, 1, 30):
                print('debug')
            # d = pd.Timestamp(datetime(2014, 1, 30))

            loc = his_tamp.index(d)
            # 如果
            if loc != 0 and not isinstance(pre_w, pd.Series):
                wei_df = pd.read_csv(os.path.join(save_path, '股票权重.csv'), engine='python')
                wei_df.set_index(wei_df.columns[0], inplace=True)
                wei_df.columns = pd.to_datetime(wei_df.columns)
                pre_w = wei_df[his_tamp[loc - 1]]

            print(d)
            r_tmp = stock_pool[d]
            e_tmp = self.E_dict[d]
            bench_wei = self.index_wei[d].dropna()
            f_n = self.control_factor.keys()
            limit_factor_df = self.factors_panel[d][f_n]
            in_benchmark = in_benchmark

            wei = optimization_fun(r_tmp, e_tmp, bench_wei, pre_w=pre_w, lam=lamda, turnover=turnover, te=te,
                                   industry_expose_control=industry_expose_control,
                                   industry_max_expose=industry_max_expose, control_factor_dict=self.control_factor,
                                   limit_factor_df=limit_factor_df, in_benchmark=in_benchmark,
                                   in_benchmark_wei=in_benchmark_wei, max_num=max_num, s_max_type=s_type)
            wei_df = pd.concat([wei_df, pd.DataFrame({d: wei})], axis=1)
            # wei_df.to_csv(os.path.join(save_path, '股票权重.csv'), encoding='gbk')
            # todo pre_w应该是经过一个月的价格变动后的权重，优化后的权重要变化的。
            pre_w = self.wei_after_a_month(wei, d)

        wei_df.fillna(0, inplace=True)
        print('全部优化完毕')
        return wei_df

    # 月初的权重，经过一个月的变动后月末的权重结果
    def wei_after_a_month(self, wei, d):
        pct = self.pet_chg_m[d]
        pct = 1 + pct / 100
        new_wei = wei * pct[wei.index]
        new_wei = new_wei / new_wei.sum()
        return new_wei

    def pool_inter(self, pool_1, pool_2):

        # 当df里面是float，而需要转换成bool型的时候的辅助函数，对为0的转换为false,非0的数据转换成True
        if type(pool_1.values[0, 0]) != bool:
            pool_1 = pool_1.applymap(lambda x: True if x != 0.0 else False)
        if type(pool_2.values[0, 0]) != bool:
            pool_2 = pool_2.applymap(lambda x: True if x != 0.0 else False)

        ret = pool_1 & pool_2
        return ret

    # 个股形态过滤
    def pattern_filter(self, pool):
        pa_res = pattern_filter()
        pa_res, pool = align(pa_res, pool)
        res_pool = pool & pa_res

        return res_pool

    def index_filter(self, pool):
        pa_res = index_filter()

        res_pool = pd.DataFrame()
        for col in pool.columns:
            if col in pa_res.index and pa_res[col]:
                # 不能开仓的日期，全部是False
                res_pool = pd.concat([res_pool, pd.DataFrame(False, index=pool.index, columns=[col])],
                                     axis=1)
            else:
                # 其余的日期直接复制
                res_pool = pd.concat([res_pool, pool[[col]]], axis=1)

        return res_pool

    # 根据给定的行业权重，和系数，得到一个行业权重范围的dict
    def ge_i_wei_with_basic(self, basic_wei, indus_max_expose=0.15):

        wei_dict = {}
        for i in basic_wei.index:
            max_w = basic_wei[i] + indus_max_expose
            min_w = max(basic_wei[i] - indus_max_expose, 0)
            wei_dict.update({i: (min_w, max_w)})

        return wei_dict

    # 根据明确给定的行业权重，得到一个行业权重范围的dict，行业权重需要读取本地文件才可以
    def ge_i_wei_with_clear_infor(self, basic_wei, indus_max_expose=0.15, path=None):

        if not path:
            path = r'D:\Database_Stock\Data\行业权重配置.xlsx'

        wei_indus = pd.read_excel(path, encoding='gbk')
        wei_indus.set_index(wei_indus.columns[0], inplace=True)

        wei_dict = {}
        for i in basic_wei.index:
            if i in wei_indus.index:
                max_w = wei_indus.loc[i, 'max']
                min_w = wei_indus.loc[i, 'min']
                wei_dict.update({i: (min_w, max_w)})
            else:
                max_w = basic_wei[i] + indus_max_expose
                min_w = max(basic_wei[i] - indus_max_expose, 0)
                wei_dict.update({i: (min_w, max_w)})

        return wei_dict

    # 个股权重分配
    def wei_allocation(self, stock_pool, indus_wei, indus_max_expose, max_num=50):

        res_wei = pd.DataFrame()
        for col in stock_pool.columns:
            indus_tmp = indus_wei.loc[:, col]
            ret = stock_pool[col]
            ret = ret.dropna()

            # 生成行业权重的字典，key为行业名称，value为tuple，（min, max）
            if col != stock_pool.columns[-1]:
                wei_dict = self.ge_i_wei_with_basic(basic_wei=indus_tmp, indus_max_expose=indus_max_expose)
            elif col == stock_pool.columns[-1]:
                wei_dict = self.ge_i_wei_with_clear_infor(basic_wei=indus_tmp, indus_max_expose=indus_max_expose)

            wei_se = optimization_fun_v2(ret, industry_wei_dict=wei_dict, max_num=max_num)

            wei_df = pd.DataFrame({col: wei_se})
            res_wei = pd.concat([res_wei, wei_df], axis=1)

        res_wei = res_wei.fillna(0)

        # (res_wei>0).sum()

        return res_wei

    def rps_filter(self, stock_pool):
        rps = self._data.RPS
        res = pd.DataFrame()
        for col, se in stock_pool.iteritems():
            rps_tmp = rps[col]
            all = pd.concat([se, rps_tmp], axis=1)
            all.columns = [col, 'rps']
            all.loc[all['rps'] < 80, col] = np.nan

            res = pd.concat([res, all[col]], axis=1)

        return res

    def run_test(self):

        # 负向指标选股，剔除一些不好的股票
        # todo, 有个代码编程时的逻辑问题，这个函数里面给self.pool赋值了，主要是未来遍历方面，后面还用到了，
        #  导致不跑这个函数就没法实现后面的一些遍历
        pool_1 = self.get_stock_pool_by_removing()
        # pool_2 = self.size_filter(top_quantile=0.7)
        # pool_1 = self.pool_inter(pool_1, pool_2)

        # # 个股过滤
        # pool_1 = self.sigal_stock_filter(pool_1, self.banned_stock_list)

        # 因子合成
        self.concate_factor()

        # 如果使用打分法，那么需要计算新合成因子的ICIR值，在确定权重部分会使用到
        if self.rov_method == 'score':
            self.compute_icir()

        # 正向选股，打分法或者是回归法，返回股票的打分或者是回归得到的预测收益率，
        stock_pool = self.rov_of_all_stocks(self.rov_method)
        stock_pool_value = copy.deepcopy(stock_pool)
        pool_2 = self.select_top_n(stock_pool, top_percent=0.1, by_industry=True)

        # fake_pool1 = self.equal_allocation(pool_2)
        # fake_pool_secname1 = pool_2_secname(fake_pool1)
        # fake_pool_secname1.to_csv(r'D:\Database_Stock\各行业排名前10%的股票名称.csv', encoding='gbk')
        # (pool_2 > 0).sum()

        stock_pool = self.pool_inter(pool_2, pool_1)

        if self.industry_dict['handle_type'] != 'keep':
            # 剔除波动率较高的个股
            to_del_stocks = self.factor_top_n(stock_pool, 'STD_1M'.upper(), 50, top_or_down='TOP')
            to_del_stocks = np.bitwise_not(to_del_stocks.fillna(False))
            stock_pool = self.pool_inter(pool_2, to_del_stocks)

        # stock_pool.sum()

        # 再选择长期动量较高的个股
        stock_pool = self.factor_top_n(stock_pool, 'RPS'.upper(), 50, top_or_down='TOP')
        # fake_pool2 = self.equal_allocation(stock_pool)
        # fake_pool_secname2 = pool_2_secname(fake_pool2)
        # fake_pool_secname2.to_csv(r'D:\Database_Stock\基本面好但高波动的股票池.csv', encoding='gbk')

        # 个股过滤，针对一些经过公司研究后的特定股票。
        # stock_pool = self.rps_filter(stock_pool)

        stock_pool = self.equal_allocation(stock_pool)

        # tmp_df = pd.DataFrame()
        # for col in stock_pool.columns:
        #     if col in stock_pool_value.columns:
        #         value_tmp = stock_pool_value[col]
        #         tof_tmp = stock_pool[col]
        #         all_tmp = pd.concat([value_tmp, tof_tmp], axis=1)
        #         all_tmp.columns = ['value', 'tof']
        #         all_tmp.loc[all_tmp['tof'] == False, 'value'] = np.nan
        #         tmp_df = pd.concat([tmp_df, pd.DataFrame({col: all_tmp['value']})], axis=1)
        # stock_pool = self.wei_allocation(stock_pool, self.index_wei, 0.1, 50)

        # if self.score_mode == 'rps':
        #
        #     # 每期的股票数量
        #     print('每期的股票数量为{}'.format(pool_intered.sum()))
        #     pool = self.factor_top_n(pool_intered, 'RPS'.upper(), 50, 6)
        #     # stock_pool = self.wei_allocation(tmp_df, self.index_wei, 0.1, 50)
        #     # 等权配置
        #     stock_pool = self.equal_allocation(pool_intered)
        #     # self.freq = 'M'
        #     stock_pool.sum()
        #
        #     # self.precise_control(tmp_df, stock_pool, self.index_wei)
        #     # self.freq = 'W'
        #     # pool_intered.sum()
        #     # stock_pool = self.rps_select(pool_intered, rps_para=0)
        #
        # elif self.score_mode == 'each_industry':
        #
        #     # 删除其他行业的股票
        #     new_cols = [col for col in stock_pool.columns if col in pool_1.columns]
        #     pool_1 = pool_1[new_cols]
        #     stock_pool = stock_pool[new_cols]
        #
        #     new_pool = pd.DataFrame()
        #     for col in stock_pool:
        #         tof_tmp = pool_1[col]
        #         sco_tmp = stock_pool[col]
        #         sco_tmp[tof_tmp == False] = np.nan
        #
        #         new_pool = pd.concat([new_pool, pd.DataFrame({col: sco_tmp})], axis=1)
        #
        #     new_pool = new_pool.dropna(axis=0, how='all')
        #
        #     stock_pool = self.select_top_n(new_pool, top_percent=self.top_percent_n, by_industry=False)
        #     # 等权配置
        #     # stock_pool = self.equal_allocation(stock_pool)
        #
        # # 选打分拍前N的股票，并等权配置得到权重
        # # elif self.score_mode == 'all_same'  self.rov_method == 'score' and self.use_risk_model == False and  \
        # #         self.select_type == 'total_num':
        # elif self.score_mode == 'all_same':
        #     stock_pool = self.select_top_n(stock_pool, by_industry=False)

        print('选股完毕，开始回测.....')
        stock_pool.sum()
        self.final_stock_pool = stock_pool

        res_dict = self.backtest(stock_pool, bench=self.benchmark, plt=False, fee='No_fee')

        return res_dict

    def latest_wei_and_stock(self):
        latest_df = self.final_stock_pool[[self.final_stock_pool.columns[-1]]]
        # latest_df.sum()
        latest_df = latest_df.where(latest_df > 0.0, None)
        latest_df = latest_df.dropna()

        data = Data()
        stock_basic = data.stock_basic_inform
        res = stock_basic.loc[latest_df.index, 'SEC_NAME']
        res_df = pd.DataFrame({'SEC_NAME': res})
        res_df.index.name = 'CODE'
        latest_df = pd.concat([latest_df, res_df], axis=1)
        return latest_df

    # 存储相关结果
    def save(self, pool, save_name='股票池每期结果.csv'):
        basic_save_path = r'D:\pythoncode\IndexEnhancement\股票池'
        save_each_sec_name(pool, save_name)

        # 把最新股票池结果存成txt, 并下载股票池基本信息
        to_save = list(pool.index[pool[pool.columns[-1]] == True])
        add_stock_pool_txt(to_save, '盈利成长选股策略_排名前50', renew=True)
        info = section_stock_infor(to_save)
        info.to_csv(os.path.join(basic_save_path, '股票基本信息.csv'), encoding='gbk')

    # 得到最新股票池
    def latest_pool(self):

        # 负向指标选股，剔除一些不好的股票
        pool_1 = self.get_stock_pool_by_removing()
        # pool_2 = self.size_filter(top_quantile=0.7)
        # pool_1 = self.pool_inter(pool_1, pool_2)
        # 个股过滤
        # pool_1 = self.sigal_stock_filter(pool_1, self.banned_stock_list)

        # 因子合成
        self.concate_factor()

        # 如果使用打分法，那么需要计算新合成因子的ICIR值，在确定权重部分会使用到
        if self.rov_method == 'score':
            self.compute_icir()

        # 正向选股，打分法或者是回归法，返回股票的打分或者是回归得到的预测收益率，
        stock_pool = self.rov_of_all_stocks(self.rov_method)

        # 各行业排名前10%
        pool_2 = self.select_top_n(stock_pool, top_percent=0.1, by_industry=True)

        # 剔除波动率较高的个股
        to_del_stocks = self.factor_top_n(pool_2, 'STD_1M'.upper(), 0.1, top_or_down='TOP')
        to_del_stocks = np.bitwise_not(to_del_stocks.fillna(False))
        stock_pool = self.pool_inter(pool_2, to_del_stocks)

        # 再选择长期动量较高的个股
        stock_pool = self.factor_top_n(stock_pool, 'RPS'.upper(), 50, top_or_down='TOP')

        codes_list = list(stock_pool.index[stock_pool[stock_pool.columns[-1]] == True])

        final_list = self.codes_replace(codes_list)

        res = self.pool_infor(final_list)

        return res

    # 得到特定月份的股票池
    def special_month_pool(self, special_date, stock_pool=None):
        if not isinstance(stock_pool, pd.DataFrame):
            self.get_stock_pool()
            stock_pool = self.main_of_select_stocks_by_scores()

        if stock_pool.empty:
            return None

        ff = None
        for c in stock_pool.columns:
            if c.year == special_date.year and c.month == special_date.month:
                ff = c
                break

        tmp = stock_pool[ff]
        s_list = list(tmp.index[tmp == True])
        return s_list


def growth_stock_pool(method='score', score_m='all_same', select_type='by_industry', risk_model=False,
                      bt_or_latest='bt', special_market_dict={}, update_ornot='update', para_d=None, freq='M',
                      indus_d=None, bm='ZZ500', start_d=None, fd_for_scores=None, percent_n=0.1, rps_para=80,
                      risk_factor=None, financial_dict=None, concate_dict=None, indus_wei=None):
    # 财务绝对要求条件的股票池构建，如roettm大于5%，eps同比增速大于5，sue位于所有股票的top5%。
    if not financial_dict:
        financial_dict = {'all': {'scope_0': ('roettm', 0, np.nan),
                                  'scope_1': ('basicepsyoy', 0, 500),
                                  # 'scope_2': ('netprofitgrowrate', 5, 500),  # 净利润同比增长率
                                  # 'scope_3': ('debtassetsratio', 1, 60),
                                  # 'rise_0': ('netprofitgrowrate', 2)
                                  }
                          }

    # indus_dict = {'to_handle_indus': ['有色金属', '钢铁', '采掘', '非银金融'],
    #               # keep 和 delete 两种模式， keep模型下，保留下来上个变量定义的子行业，delete模式下，删除上面的子行业
    #               'handle_type': 'delete',
    #               }

    # 仅对家用电器做一下，看看结果如何
    # indus_dict = {'keep': '家用电器'}
    select_m = select_type  # 'total_num'
    if not fd_for_scores:
        print('fd_for_scores 未定义')
        raise ValueError

    para_d = None
    control_factor = None

    pool = FormStockPool(financial_dict, use_risk_model=risk_model, start_date=start_d, factor_concate_dict=concate_dict,
                         factor_dict_for_scores=fd_for_scores, special_market_dict=special_market_dict,
                         risk_factor=risk_factor, control_factor=control_factor, opt_para_dict=para_d, method=method,
                         score_mode=score_m, select_mode=select_m, benchmark=bm, freq=freq,
                         percent_n=percent_n, industry_dict=indus_d, update_ornot=update_ornot, rps_para=rps_para)

    if isinstance(wei, pd.DataFrame):
        pool.set_indus_wei(wei)

    if bt_or_latest == 'latest':
        newest_pool = pool.latest_pool()
        return newest_pool

    elif bt_or_latest == 'bt':
        res_dict = pool.run_test()
        wei_latest = pool.latest_wei_and_stock()
        res_dict.update({'最新一期的权重': wei_latest})

        return res_dict


if '__main__' == __name__:

    indus_d = {'handle_type': 'delete',
               'to_handle_indus': ['国防军工', '银行', '非银金融', '钢铁']
               }

    method = 'score'
    special_market_dict = None
    score_mode = 'rps'       #    'rps'  # 'all_same'   'rps'
    use_risk_model = False
    select_type = 'total_num'
    start_date = datetime(2012, 1, 1)
    my_freq = 'M'
    bt_or_latest = 'bt'  # 'bt' , 'latest' ，'only_pool'
    update_ornot = 'update'   # 'renew'  'update'
    rps_para = 85

    financial_dict = {'all': {'scope_0': ('roettm', 0, np.nan),
                              'scope_1': ('basicepsyoy', 0, 500),
                              # 'scope_2': ('netprofitgrowrate', 5, 500),  # 净利润同比增长率
                              # 'scope_3': ('debtassetsratio', 1, 60),
                              # 'rise_0': ('netprofitgrowrate', 2)
                              }
                      }

    para_dict = None
    risk_factor = None

    # fd = {'default': list(default_dict.keys())}
    # concate_dict = {'default': default_dict}
    concate_dict = factor_dict_to_concate
    fd = {}
    for key, values in factor_dict_to_concate.items():
        fd.update({key: list(values.keys())})

    # 如果需要剔除某些行业或者保留某些行业，可以设置indus_d参数， 如仅保留家电，可设置为：indus_d = {'keep': '家用电器'}
    # 如果需要剔除钢铁，则 indus_d = {'delete': '钢铁'}
    # select_type = None                            # 'total_num' 'twice_sort' None
    # bt_or_latest = 'bt'                           # 'bt' 'latest'  'latest_pool_daily'

    percent_n = 0.1
    if 'bm' not in dir():
        bm = None
    if 'rps_para' not in dir():
        rps_para = None
    if 'use_risk_model' not in dir():
        use_risk_model = False
    if 'risk_factor' not in dir():
        risk_factor = None

    # 设定行业基准
    p = r'D:\Database_Stock\Data\fund\indus_wei_ma.csv'
    wei = pd.read_csv(p, encoding='gbk')
    wei = wei.set_index(wei.columns[0])
    wei.columns = pd.to_datetime(wei.columns)
    wei = wei.dropna(how='all', axis=1)

    res_dict = growth_stock_pool(method=method, score_m=score_mode, select_type=select_type, indus_wei=wei,
                            risk_model=use_risk_model, bt_or_latest=bt_or_latest, freq=my_freq,
                            risk_factor=risk_factor, special_market_dict=special_market_dict,
                            update_ornot=update_ornot, para_d=para_dict, indus_d=indus_d,
                            bm=bm, start_d=start_date, fd_for_scores=fd, percent_n=percent_n,
                            rps_para=rps_para, financial_dict=financial_dict, concate_dict=concate_dict)

    save_path = r'D:\Database_Stock\临时'
    if bt_or_latest == 'bt':  # 'bt' , 'latest'

        ana = res_dict['指标']
        nv = res_dict['净值']
        each_year = res_dict['历年表现']
        each_month = res_dict['每月表现']
        fig = res_dict['净值走势图']
        pool_secname = res_dict['每期股票名称']
        latest_wei = res_dict['最新一期的权重']

        ana.to_csv(os.path.join(save_path, '指标.csv'), encoding='gbk')
        nv.to_csv(os.path.join(save_path, '净值.csv'), encoding='gbk')
        fig.savefig(os.path.join(os.path.join(save_path, '净值走势图.png')))
        plt.close()
        each_month.to_csv(os.path.join(save_path, '每月表现.csv'), encoding='gbk')
        each_year.to_csv(os.path.join(save_path, '历年表现.csv'), encoding='gbk')
        pool_secname.to_csv(os.path.join(save_path, '历期股票池.csv'), encoding='gbk')
        latest_wei.to_csv(os.path.join(save_path, '最近一期股票池.csv'), encoding='gbk')

    elif bt_or_latest == 'latest':
        newest_pool = res
        newest_pool.to_csv(r'D:\Database_Stock\股票池_最终\最新股票池.csv', encoding='gbk')
    elif bt_or_latest == 'only_pool':
        stock_pool = res
        stock_pool.to_csv(os.path.join(save_path, '历史每期股票池_week.csv'), encoding='gbk')









