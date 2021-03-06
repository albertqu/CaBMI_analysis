# 2p to record the neuronal data
# Scanimage defines the format of data

__author__ = 'Nuria'

# C++ extension for data acquisition

# system
import sys
import h5py
import os, re
import time
import math
import random
import copy
import shutil, traceback
import multiprocessing as mp

# data
from skimage import io
import numpy as np
import pandas as pd
import scipy
from scipy import stats
from scipy import interpolate
from scipy.stats.mstats import zscore
from scipy.signal import correlate
import sklearn
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression

# caiman
try:
    import caiman as cm
    from caiman.motion_correction import MotionCorrect
    from caiman.components_evaluation import estimate_components_quality_auto
    from caiman.source_extraction.cnmf.deconvolution import constrained_foopsi
except ModuleNotFoundError:
    print("CaImAn not installed or environment not activated, certain functions might not be usable")

# plotting
import pdb
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.axes_grid1 import make_axes_locatable
from mpl_toolkits.axes_grid1 import host_subplot
from matplotlib.ticker import MultipleLocator
try:
    import seaborn as sns
    PALETTE = [sns.color_palette('Blues')[-1], sns.color_palette('Reds')[-1]] # Blue IT, Red PT
except ModuleNotFoundError:
    print('seaborn not installed')
from matplotlib import interactive
interactive(True)

# utils
import utils_cabmi as ut
from utils_loading import get_PTIT_over_days, parse_group_dict, encode_to_filename, load_A
from utils_loading import path_prefix_free, file_folder_path
from utils_cabmi import std_filter, median_filter
from pipeline import separate_planes, separate_planes_multiple_baseline
from preprocessing import get_roi_type


def learning(folder, animal, day, sec_var='', to_plot=True, out=None):
    '''
    Obtain the learning rate over time.
    Inputs:
        FOLDER: String; path to folder containing data files
        ANIMAL: String; ID of the animal
        DAY: String; date of the experiment in YYMMDD format
        TO_PLOT: Boolean; whether or not to plot the changing learning rate
    Outputs:
        HPM: Numpy array; hits per minute
        TTH: Numpy array; time to hit (in frames)
        PERCENTAGE_CORRECT: float; the proportion of hits out of all trials
    '''
    if out is None:
        out = folder
    out_analysis = os.path.join(out, 'analysis/learning/', animal, day)
    f = h5py.File(encode_to_filename(os.path.join(folder, processed), animal, day), 'r')
    fr = f.attrs['fr']
    blen = f.attrs['blen']
    hits = np.asarray(f['hits'])
    miss = np.asarray(f['miss'])
    array_t1 = np.asarray(f['array_t1'])
    array_miss = np.asarray(f['array_miss'])
    trial_end = np.asarray(f['trial_end'])
    trial_start = np.asarray(f['trial_start'])
    percentage_correct = hits.shape[0]/trial_end.shape[0]
    bins = np.arange(0, trial_end[-1]/fr, 60)
    [hpm, xx] = np.histogram(hits/fr, bins)

    tth = trial_end[array_t1] + 1 - trial_start[array_t1]
    
    if to_plot:
        fig1 = plt.figure()
        ax = fig1.add_subplot(121)
        sns.regplot(xx[1:]/60.0, hpm, label='hits per min')
        ax.set_xlabel('Minutes')
        ax.set_ylabel('Hit Rate (hit/min)')
        ax1 = fig1.add_subplot(122)
        sns.regplot(np.arange(tth.shape[0]), tth, label='time to hit')
        ax1.set_xlabel('Reward Trial')
        ax1.set_ylabel('Number of Frames')
        ax1.yaxis.set_label_position('right')
        if out is None:
            out=folder
            if not os.path.exists(out_analysis):
                os.makedirs(out_analysis)
            fig1.savefig(out_analysis + 'hpm.png', bbox_inches="tight")
    return hpm, tth, percentage_correct


def learning_params(
    folder, animal, day, sec_var='', bin_size=1,
    to_plot=None, end_bin=None, reg=False, dropend=True, out=None):
    '''
    Obtain the learning rate over time, including the fitted linear regression
    model. This function also allows for longer bin sizes.
    Inputs:
        FOLDER: String; path to folder containing data files
        ANIMAL: String; ID of the animal
        DAY: String; date of the experiment in YYMMDD format
        BIN_SIZE: The number of minutes to bin over. Default is one minute
        TO_PLOT: Bool; whether to generate hpm plots or not.
    Outputs:
        HPM: Numpy array; hits per minute
        PERCENTAGE_CORRECT: float; the proportion of hits out of all trials
        REG: The fitted linear regression model
    '''
    if out is None:
        out = folder
    f = h5py.File(encode_to_filename(os.path.join(folder, 'processed'), animal, day), 'r')
    fr = f.attrs['fr']
    blen = f.attrs['blen']
    blen_min = blen//600
    hits = np.asarray(f['hits'])
    miss = np.asarray(f['miss'])
    array_t1 = np.asarray(f['array_t1'])
    array_miss = np.asarray(f['array_miss'])
    trial_end = np.asarray(f['trial_end'])
    trial_start = np.asarray(f['trial_start'])
    #percentage_correct = hits.shape[0]/trial_end.shape[0]
    bigbin = bin_size*60
    if dropend:
        ebin = trial_end[-1]/fr + 1
    else:
        ebin = int(np.ceil(trial_end[-1]/fr / bigbin)) * bigbin + 1
    bins = np.arange(0, ebin, bigbin)
    [hpm, xx] = np.histogram(hits/fr, bins)
    [mpm, _] = np.histogram(miss/fr, bins)
    hpm = hpm[blen_min//bin_size:]
    mpm = mpm[blen_min//bin_size:]
    percentage_correct = hpm / (hpm+mpm)
    # TODO: CONSIDER SETTING THRESHOLD USING ONLY THE WHOLE WINDOWS
    xx = -1*(xx[blen_min//bin_size]) + xx[blen_min//bin_size:]
    xx = xx[1:]
    if not dropend:
        last_binsize = (trial_end[-1] / fr - bins[-2])
        hpm[-1] *= bigbin / last_binsize
        xx[-1] = last_binsize + xx[-2]
    # TODO: ADD BACK THE LAST BIN
    hpm = hpm / bin_size
    if end_bin is not None:
        end_frame = end_bin//bin_size + 1
        hpm = hpm[:end_frame]
        xx = xx[:end_frame]
    tth = trial_end[array_t1] + 1 - trial_start[array_t1]
    
    if to_plot is not None:
        maxHit, hitIT_salient, hitPT_salient, hit_all_salient, hit_all_average, pcIT_salient, pcPT_salient, pc_all_salient, pc_all_average= \
            to_plot
        out = os.path.join(out, 'learning/plots/evolution_{}/'.format(bin_size))
        if not os.path.exists(out):
            os.makedirs(out)
        fig1 = plt.figure(figsize=(15, 5))
        ax = fig1.add_subplot(131)
        ax.axhline(hitIT_salient, color=PALETTE[0], lw=1.25, label='IT salience')
        ax.axhline(hitPT_salient, color=PALETTE[1], lw=1.25, label='PT salience')
        ax.axhline(hit_all_salient, color='yellow', lw=1.25, label='All salience')
        ax.legend()
        sns.regplot(xx/60, hpm, label='hits per min')
        ax.set_xlabel('Minutes')
        ax.set_ylabel('Hit Rate (hit/min)')
        ax.set_title('Hit Rate Evolution')
        if maxHit is not None:
            ax.set_ylim((-maxHit / 20, maxHit * 21 / 20))


        # TODO: ADD CHANCE LEVEL
        ax1 = fig1.add_subplot(132)
        sns.regplot(np.arange(tth.shape[0]), tth / fr, label='time to hit')
        ax1.set_xlabel('Reward Trial')
        ax1.set_ylabel('Hit Time (second)')
        ax1.set_title('Hit Time Evolution')
        ax1.set_ylim((-1.5, 31.5))
        ax1.legend()
        ax2 = fig1.add_subplot(133)
        ax2.axhline(pcIT_salient*100, color=PALETTE[0], lw=1.25, label='IT salience')
        ax2.axhline(pcPT_salient*100, color=PALETTE[1], lw=1.25, label='PT salience')
        ax2.axhline(pc_all_salient*100, color='yellow', lw=1.25, label='All salience')
        sns.regplot(xx/60, percentage_correct * 100, label='percentage correct')
        ax2.set_xlabel('Minutes')
        ax2.set_ylabel('Percentage Correct (%)')
        ax2.set_title('Percentage Correct Evolution')
        ax2.set_ylim((-5, 105))
        ax2.legend()
        learner_pc = -1 # Good learner: 2, Average Learner: 1, Bad Learner: 0, Non Learner: -1
        learner_hpm = -1
        pcmax = np.nanmax(percentage_correct)
        nmax = np.nanmax(hpm)
        if pcmax >= pc_all_salient:
            learner_pc = 2
        elif pcmax >= pc_all_average:
            learner_pc = 1
        elif pcmax >= 0.3:
            learner_pc = 0

        if nmax >= hit_all_salient:
            learner_hpm = 2
        elif nmax >= hit_all_average:
            learner_hpm = 1
        elif nmax >= 1: # 1 as learning criteria
            learner_hpm = 0
        # HERE DEFINE LEARNING VALUE [0.7, 0.3]
        fig1.savefig(
            out + "L_{}_{}_{}_evolution_{}.png".format(int(pcmax>=0.7)+int(pcmax>=0.3),animal, day, bin_size),
            bbox_inches="tight"
            )
        plt.close('all')

    xx_axis = xx/bigbin
    xx_axis = np.expand_dims(xx_axis, axis=1)
    return xx_axis, hpm, percentage_correct, LinearRegression().fit(xx_axis, hpm) if reg else None

def activity_hits(folder, animal, day, sec_var=''):
    '''
    Function to obtain the activity of neurons time-locked to the trial end.
    Inputs:
        FOLDER: String; path to folder containing data files
        ANIMAL: String; ID of the animal
        DAY: String; date of the experiment in YYMMDD format 
    '''
    folder_path = folder +  'processed/' + animal + '/' + day + '/'
    f = h5py.File(
        folder_path + 'full_' + animal + '_' + day + '_' +
        sec_var + '_data.hdf5', 'r'
        )
    C = np.asarray(f['C'])

#def neuron_activity(folder, animal, day, sec_var='', to_plot=True):
    # Function to calculate and plot the CAsignals of each neuron
    # separate ensamble/indirect neurons, hits/miss, IT/PT/REST
    # in average (of all trials) + evolution over trials 


def frequency_tuning(folder, animal, day, to_plot=True):
    #function to check if there is any tuning to different frequencies
    folder_path = folder +  'processed/' + animal + '/' + day + '/'
    folder_dest = folder +  'analysis/' + animal + '/'
    if not os.path.exists(folder_dest):
        os.makedirs(folder_dest)
    f = h5py.File(
        folder_path + 'full_' + animal + '_' + day + '_' +
        sec_var + '_data.hdf5', 'r'
        )
    frequency_data = np.asarray(f['frequency'])
    dff_data = np.asarray(f['dff'])
    blen = f.attrs['blen']
    end_trial = f['trial_end'][0] + 1
    
    f = h5py.File(
        folder_dest + 'tuning_' + animal + '_' + day + '_' +
        sec_var + '_freq.hdf5', 'w-'
        )
    
    dff = dff_data[:, blen:]
    frequencies = np.unique(frequency_data)
    num_neurons = dff.shape[0]
    num_frequencies = frequencies.shape[0]
    num_trials = end_trial.shape[0]
    tuning_session = np.ones((num_neurons, num_frequencies)) + np.nan
    tuning_session_er = np.ones((num_neurons, num_frequencies)) + np.nan
    tuning_trial = np.ones((num_neurons, num_trials, num_frequencies)) + np.nan
    ind_ts = np.ones(frequencies.shape[0]) + np.nan 
    ind_tt = np.ones((end_trial, frequencies.shape[0])) + np.nan
    
    # control check, frequency should be as long as dff
    if frequency.shape[0] != dff.shape[1]:
        print('Warning: arrays length mismatch')
        frequency = frequency[:dff.shape[1]]
        
    # to calculate average dff for different frequencies
    for ind,freq  in enumerate(frequencies):
        ind_freq = np.flatnonzero(frequency_data == freq)
        tuning_session[:, ind] = np.nanmean(dff[:, ind_freq], 1)
        tuning_session_er[:, ind] = \
            pd.DataFrame(dff[:, ind_freq]).sem(0).values # std of the mean
        ind_ts[ind] = ind_freq.shape[0]
    
    # to calculate dff for different frequencies over trials
    trials = np.concatenate(([[0], end_trial[:-1]]))
    for tt, trial in enumerate(trials):
        aux_dff = dff[:, trial:end_trial[tt]]
        aux_freq = frequency_data[trial:end_trial[tt]]
        for ind,freq in enumerate(frequencies):
            ind_freq = np.flatnonzero(aux_freq == freq)
            tuning_trial[:, tt, ind] = np.nanmean(aux_dff[:, ind_freq], 1)
            ind_ts[tt, ind] = ind_freq.shape[0]

    f.create_dataset('tuning_session', tuning_session)
    f.create_dataset('tuning_trial', tuning_trial)
    f.create_dataset('ind_ts', ind_ts)
    f.create_dataset('ind_tt', ind_tt)
    
    if to_plot:
        for n in np.arange(dff.shape[0]):
            sm_data = ut.sliding_mean(tuning_session[n,:], window=window_sld)
            sm_error = sliding_mean(tuning_session_er[n,:], window=window_sld)
            plt.fill_between(
                frequency_values, sm_data - sm_error, sm_data + sm_error,
                color="#3F5D7D"
                )
            plt.plot(frequency_values, sm_data, color="white", lw=1)
            plt.savefig(
                folder_dest + day + '_' + n + '_smtuning_curve.png',
                bbox_inches="tight"
                )


def feature_select(folder, animal, day, sec_var='', sec_bin=[30, 0], step=5,
    score_min=0.9, toplot=True):
    """Function to select neurons that are relevant to the task, it goes iteratively through a
    temporal vector defined by sec_bin with bins of step
    folder (str): folder where the input/output is/will be stored 
    animal (str): animal to be analyzed
    day (str): day to be analyzed
    sec_var (str): secondary variable to identify type of experiment
    sec_bin (tuple): frames before and after exp.
    score_min: minimum value to consider the feature selection
    return 
    sel_neurs: an array of the fitted RFECV models
    neur : index of neurons to consider
    and number of neurons selected
    """
    folder_path = folder +  'processed/' + animal + '/' + day + '/'
    f = h5py.File(folder_path + 'full_' + animal + '_' + day + '__data.hdf5','r')
 
    # obtain C divided by trial
    C_ord = ut.time_lock_activity(f, sec_bin)
    array_t1 = np.asarray(f['array_t1'])
    
    # trial label 
    classif = np.zeros(C_ord.shape[0])
    classif[array_t1] = 1
    
    # steps to run throuhg C_ord
    steps = np.arange(0, np.nansum(sec_bin) + step, step)
        
    # prepare models
    lr = sklearn.linear_model.LogisticRegression()
    selector = sklearn.feature_selection.RFECV(
        lr, step=1, cv=5, scoring='balanced_accuracy'
        )
    
    # init neur
    neur = np.zeros(C_ord.shape[1]).astype('bool')
    succesful_steps = np.zeros(steps.shape[0])
    sel_neurs = []
    
    # run models through each step
    for ind, s in enumerate(steps[1::]):
        data = np.nansum(C_ord[:, :, steps[ind]:s], 2)
        sel_neur = selector.fit(data, classif)
        # if the info is good keep the neurons
        if max(sel_neur.grid_scores_) > score_min:
            neur = np.logical_or(sel_neur.support_, neur)
        succesful_steps[ind] = max(sel_neur.grid_scores_)
        if toplot:
            plt.plot(sel_neur.grid_scores_, label=str(ind))
        sel_neurs.append(sel_neur)
    
    if toplot:
        plt.legend()
    
    return sel_neurs, neur, np.sum(neur)


# def tdmodel(folder, animal, day, sec_var='', to_plot=True):
    # Create TD model and compare V(t) and d(T) to activity of neurons
    # IT/PT/REST

def raw_activity_tuning_single_session(folder, animal, day, i, window=3000, itype='dff', metric='raw', zcap=None):
    hf = encode_to_filename(processed, animal, day)
    if not os.path.exists(hf):
        print("Not found:, ", hf)
    with h5py.File(hf, 'r') as fp:
        S = np.array(fp[itype])
        # TODO: maybe include trial activity tuning
        #array_hit, array_miss = np.array(fp['array_t1']), np.array(fp['array_miss'])
        ens_neur = np.array(fp['ens_neur'])
        e2_neur = ens_neur[fp['e2_neur']] if 'e2_neur' in fp else None
        redlabel, nerden = np.array(fp['redlabel']), np.array(fp['nerden'])
        rois = get_roi_type(fp, animal, day)
    if metric == 'mean' and zcap is not None:
        zscoreS = zscore(S, axis=1)
        if zcap != -1:
            zscoreS = np.minimum(zscoreS, np.full_like(S, zcap))
    else:
        zscoreS = S
    N = S.shape[0]
    nsessions = S.shape[1] // window
    remainder = S.shape[1] - nsessions * window
    Sfirst = (zscoreS[:, :nsessions * window]).reshape((S.shape[0], nsessions, window), order='C')
    avg_S = np.nanmean(Sfirst, axis=2)
    if remainder > 0:
        Sremain = zscoreS[:, nsessions * window:]
        avg_S = np.concatenate((avg_S, np.nanmean(Sremain, keepdims=True, axis=1)), axis=1)

    # N, sw, st = probeW.shape[0], probeW.shape[1], probeT.shape[1]
    # ROI_type
    sw = nsessions + (1 if remainder else 0)
    
    # DF Window
    results = [np.tile(np.arange(sw), N), np.repeat(rois, sw), np.repeat(np.arange(N), sw), avg_S.ravel(order='C'),
    np.full(N * sw, animal[:2]), np.full(N * sw, animal), np.full(N * sw, day), np.full(N*sw, i+1)]
    print(animal, day, 'done')
    return results


def raw_activity_tuning(folder, groups, window=3000, itype='dff', metric='mean', zcap=None, test=True, nproc=1):
    # TODO: ADD OPTION TO PASS IN A LIST OF METHODS FOR COMPARING THE PLOTS!
    """Calculates Peak Timing and Stores them in csvs for all animal sessions in groups located in folder."""
    if nproc == 0:
        nproc = mp.cpu_count()
    processed = os.path.join(folder, 'CaBMI_analysis/processed')
    if groups == '*':
        all_files = get_PTIT_over_days(processed)
    else:
        all_files = {g: parse_group_dict(processed, groups[g], g) for g in groups.keys()}
    print(all_files)
    hp = 'window{}_zcap{}'.format(window, zcap)
    S_OPT = metric + 'Z' if zcap is not None else '' + itype
    Z_OPT = S_OPT + 'zcap{}'.format(zcap) if zcap != -1 else ''
    resW = {n: [] for n in ['window', 'roi_type', 'N', S_OPT, 'group', 'animal',
       'date', 'session']}
    def helper(animal):
        ress = []
        for i, day in enumerate(sorted(group_dict[animal])):
            ress.append(raw_activity_tuning_single_session(folder, animal, day, i, window, itype, metric, zcap))
        return ress
    if nproc == 1:
        for group in all_files:
            group_dict = all_files[group]
            for animal in group_dict:
                results = helper(animal)
                for result in results:
                    resW['window'].append(result[0])
                    resW['roi_type'].append(result[1])
                    resW['N'].append(result[2])
                    resW[S_OPT].append(result[3])
                    resW['group'].append(result[4])
                    resW['animal'].append(result[5])
                    resW['date'].append(result[6])
                    resW['session'].append(result[7])

                # # DF TRIAL
                # trials = np.arange(1, st + 1)
                # tempm = trials[array_miss]
                # temph = trials[array_hit]
                # misses = np.empty_like(tempm)
                # hits = np.empty_like(temph)
                # sortedm = np.argsort(tempm)
                # sortedh = np.argsort(temph)
                # for i in range(len(sortedm)):
                #     misses[sortedm[i]] = -i - 1
                # for i in range(len(sortedh)):
                #     hits[sortedh[i]] = i + 1
                # hm_trial = np.empty_like(trials)
                # hm_trial[array_hit] = hits
                # hm_trial[array_miss] = misses
                # # trials[array_miss] = -trials[array_miss]
                # # awhere = np.where(trials < 0)[0]
                # # assert np.array_equal(awhere, array_miss), "NOt alligned {} {}".format(awhere, array_miss)
                # resT['trial'] = np.tile(trials, N)  # 1-indexed
                # resT['HM_trial'] = np.tile(hm_trial, N)  # 1-indexed
                # resT['roi_type'] = np.repeat(rois, st)
                # resT['N'] = np.repeat(np.arange(N), st)
                # for k in mets_window:
                #     resW[k] = mets_window[k].ravel(order='C')
                #     resT[k] = mets_trial[k].ravel(order='C')

                # print(N, sw, st)
                # def debug_print(res):
                #     for k in res.keys():
                #         print(k, res[k].shape)
                # debug_print(resW)
                # debug_print(resT)
                # df_trial = pd.DataFrame(resT)
    else:
        p = mp.Pool(nproc)
        series = [(folder, animal, day, i, window, itype, metric, zcap) 
        for group in all_files for animal in all_files[group] for i, day in enumerate(sorted(all_files[group][animal]))]
        results = p.starmap_async(raw_activity_tuning_single_session, series).get()
        for result in results:
            resW['window'].append(result[0])
            resW['roi_type'].append(result[1])
            resW['N'].append(result[2])
            resW[S_OPT].append(result[3])
            resW['group'].append(result[4])
            resW['animal'].append(result[5])
            resW['date'].append(result[6])
            resW['session'].append(result[7])

    for k in resW:
        print(k, len(resW[k]))
        resW[k] = np.concatenate(resW[k])
        print(resW[k].shape)
    df_window = pd.DataFrame.from_dict(resW)
    if test:
        # testing = os.path.join(path, 'test.csv')
        # if os.path.exists(testing):
        #     print('Deleting', testing)
        #     os.remove(testing)
        df_window.to_csv(os.path.join(processed, '{}_activity_tuning_{}_window_{}.csv'.format(Z_OPT, metric, hp)),
                         index=False)
        # df_trial.to_csv(os.path.join(path, '{}_{}_trial_test.csv'.format(animal, day)),
        #                 index=False)
    # else:
    #     df_window.to_hdf(fname, 'df_window')
    #     # df_trial.to_hdf(fname, 'df_trial')
    return df_window


def coactivation_single_session(inputs, window=3000, mlag=10, include_dend=False, source='dff', out=None):
    # N * N * w * d
    if isinstance(inputs, np.ndarray):
        S = inputs
        animal, day = None, None
        path = './'
        savename = 'sample_{}_coactivation_w{}.dat'.format(source, window)
        if out is None:
            out = path
        savepath = os.path.join(out, savename)
    else:
        if isinstance(inputs, str):
            opts = path_prefix_free(inputs, '/').split('_')
            path = file_folder_path(inputs)
            animal, day = opts[1], opts[2]
            f = None
            hfile = inputs
        elif isinstance(inputs, tuple):
            path, animal, day = inputs
            hfile = encode_to_filename(path, animal, day)
            f = None
        elif isinstance(inputs, h5py.File):
            opts = path_prefix_free(inputs.filename, '/').split('_')
            path = file_folder_path(inputs.filename)
            animal, day = opts[1], opts[2]
            f = inputs
        else:
            raise RuntimeError("Input Format Unknown!")
        savename = '{}_{}_{}_coactivation_w{}{}.dat'\
            .format(animal, day, source, window, '_nerden' if include_dend else '')
        if out is None:
            out = path
        savepath = os.path.join(out, savename)
        if os.path.exists(savepath):
            return
        if f is None:
            f = h5py.File(hfile, 'r')

        S = np.array(f[source])
        if not include_dend:
            S = S[f['nerden']]
        f.close()
    if not os.path.exists(out):
        os.makedirs(out)
    N, T = S.shape
    nW = int(np.ceil(T/window))
    neurcorr = np.memmap(savepath, mode='w+', shape=(N, N, nW, T))
    for i in range(N):
        for j in range(N):
            for w in range(nW):
                b = window*w
                l = T-b if w == nW-1 else window
                neurcorr[i, j, w, :l] = correlate(S[i, b: b + l], S[j, b: b + l], mode='same')
    del neurcorr
    return savepath


#################################################################
######################## SNR calculation ########################
#################################################################
def extract_clean_dff(A, C, B, F, raw=True):
    nA = np.sqrt(np.ravel(A.power(2).sum(axis=0)))
    nA_mat = scipy.sparse.spdiags(nA, 0, nA.shape[0], nA.shape[0])
    nA_inv_mat = scipy.sparse.spdiags(1. / nA, 0, nA.shape[0], nA.shape[0])
    A = A * nA_inv_mat
    F0 = (A.T @ B) @ F
    if raw:
        return C / F0


def MF_infer_f0(A, C, B, Yr, efficient=True):
    # A, C, B, Yr numpy arrays of the same orders
    B_inv = np.linalg.inv(B.T @ B)
    if B.shape[0] != A.shape[0]:
        A = A.T
    if efficient:
        return B_inv @ (B.T @ Yr - (B.T @ A) @ C)
    else:
        R = Yr - A @ C
        return B_inv @ (B.T @ R)


def compute_residuals(Yr, A, C, b, f, block_size=5000, num_blocks_per_run=20, dview=None):
    # adapted from caiman/source_extraction/cnmf/cnmf.py
    """compute residual for each component (variable YrA)

     Args:
         Yr :    np.ndarray
                 movie in format pixels (d) x frames (T)

    """
    if 'csc_matrix' not in str(type(A)):
        A = scipy.sparse.csc_matrix(A)
    if 'array' not in str(type(b)):
        b = b.toarray()
    if 'array' not in str(type(C)):
        C = C.toarray()
    if 'array' not in str(type(f)):
        f = f.toarray()

    Ab = scipy.sparse.hstack((A, b)).tocsc()
    nA2 = np.ravel(Ab.power(2).sum(axis=0))
    nA2_inv_mat = scipy.sparse.spdiags(
        1. / nA2, 0, nA2.shape[0], nA2.shape[0])
    Cf = np.vstack((C, f))
    if 'numpy.ndarray' in str(type(Yr)):
        YA = (Ab.T.dot(Yr)).T * nA2_inv_mat
    else:
        YA = cm.mmapping.parallel_dot_product(Yr, Ab, dview=dview, block_size=block_size,
                                              transpose=True,
                                              num_blocks_per_run=num_blocks_per_run) * nA2_inv_mat

    AA = Ab.T.dot(Ab) * nA2_inv_mat
    return (YA - (AA.T.dot(Cf)).T)[:, :A.shape[-1]].T


def compute_SNR_from_traces(A, C, b, f, Yr, pix, fr=4, decay_time=0.4, gSig=(3, 3), min_SNR=2.5, rval_thr=0.8,
                            cnn_thr=0.8, block_size=5000, num_blocks_per_run=20, dview=None):
    """
    Args:
         Yr :    np.ndarray
                 movie in format pixels (d) x frames (T)
    compare with cnm.estimates.YrA: mean: 0.9688151430842026 max:  0.99811120903548 min:  0.8694174255835385
    """
    dims = (pix, pix)
    T = Yr.shape[1]
    images = np.reshape(Yr, dims + (T,), order='F')
    YrA = compute_residuals(Yr, A, C, b, f, block_size, num_blocks_per_run, dview)
    idx_components, idx_components_bad, SNR_comp, r_values, cnn_preds = \
        estimate_components_quality_auto(images, A, C, b, f, YrA, fr, decay_time, gSig, dims,
                                         dview=dview, min_SNR=min_SNR,
                                         r_values_min=rval_thr, use_cnn=False, thresh_cnn_min=cnn_thr)
    return SNR_comp


def estimate_SNR_hfile(fnames, hfile, fr, used_planes=1, numplanes=1, pixel=256, decay_time=0.4,
                       gSig=(3, 3), min_SNR=2.5, rval_thr=0.8, cnn_thr=0.8, block_size=5000,
                       num_blocks_per_run=20, dview=None, baseline=True, motion=True, ORDER='F'):
    """Given single plane [tiff fnames] and hfile, get SNR_comp"""

    # Params
    # motion correct
    niter_rig = 1  # number of iterations for rigid motion correction
    max_shifts = (3, 3)  # maximum allow rigid shift
    splits_rig = 10  # for parallelization split the movies in  num_splits chuncks across time
    strides = (96, 96)  # start a new patch for pw-rigid motion correction every x pixels
    overlaps = (48, 48)  # overlap between pathes (size of patch strides+overlaps)
    splits_els = 10  # for parallelization split the movies in  num_splits chuncks across time
    upsample_factor_grid = 4  # upsample factor to avoid smearing when merging patches
    max_deviation_rigid = 3  # maximum deviation allowed for patch with respect to rigid shifts
    ########################

    if 'estimates' in hfile:
        hf = hfile['estimates']
        B = np.array(hf['b'])
    else:
        hf = hfile
        b = hfile['base_im']
        B = np.array(hfile['base_im']).reshape((-1, b.shape[-1]), order=ORDER)
    A = load_A(hfile)
    if A.shape[0] != B.shape[0]:
        A = A.T
    C = np.array(hf['C'])
    first_file = fnames[0]
    if isinstance(fnames, np.ndarray):
        Yr, p = fnames, pixel
    elif '.mmap' in first_file or '.tif' in first_file:
        if motion:
            print('***************Starting motion correction*************')
            print('files:')
            print(fnames)

            # %%% MOTION CORRECTION
            # first we create a motion correction object with the parameters specified
            min_mov = cm.load(fnames[0]).min()
            # this will be subtracted from the movie to make it non-negative

            mc = MotionCorrect(fnames, min_mov,
                               dview=dview, max_shifts=max_shifts, niter_rig=niter_rig,
                               splits_rig=splits_rig,
                               strides=strides, overlaps=overlaps, splits_els=splits_els,
                               upsample_factor_grid=upsample_factor_grid,
                               max_deviation_rigid=max_deviation_rigid,
                               shifts_opencv=True, nonneg_movie=True)
            # note that the file is not loaded in memory

            # %% Run piecewise-rigid motion correction using NoRMCorre
            mc.motion_correct_pwrigid(save_movie=True)
            bord_px_els = np.ceil(np.maximum(np.max(np.abs(mc.x_shifts_els)),
                                             np.max(np.abs(mc.y_shifts_els)))).astype(np.int)
            print('***************Motion correction has ended*************')
            # maximum shift to be used for trimming against NaNs

            # %% MEMORY MAPPING
            # memory map the file in order 'C'
            fnames = mc.fname_tot_els  # name of the pw-rigidly corrected file.
            # TODO: check for .mmap file loading
            fname_new = cm.save_memmap(fnames, base_name='memmap_', order='C',
                                   border_to_0=bord_px_els)  # exclude borders
        else:
            fname_new = cm.save_memmap(fnames, base_name='memmap_', order='C')

        # now load the file
        Yr, dims, T = cm.load_memmap(fname_new)
        p = dims[0]
    else:
        raise NotImplementedError("currently only supports mmap, tif, and numpy array")

    Fhat = MF_infer_f0(A, C[:, :T], B, Yr)  

    SNR_comp = compute_SNR_from_traces(A, C[:, :T], B, Fhat, Yr, p, fr=fr, decay_time=decay_time, gSig=gSig,
                                       min_SNR=min_SNR, rval_thr=rval_thr, cnn_thr=cnn_thr,
                                       block_size=block_size, num_blocks_per_run=num_blocks_per_run,
                                       dview=dview)
    if baseline:
        blen = 9000
        SNR_comp_base = compute_SNR_from_traces(A, C[:, :blen], B, Fhat[:, :blen], Yr[:, :blen], p, fr=fr, decay_time=decay_time, gSig=gSig,
                                           min_SNR=min_SNR, rval_thr=rval_thr, cnn_thr=cnn_thr,
                                           block_size=block_size, num_blocks_per_run=num_blocks_per_run,
                                           dview=dview)
        return SNR_comp, SNR_comp_base, Fhat
    return SNR_comp, Fhat


def calc_SNR_all_planes(folder, animal, day, num_files, num_files_b, number_planes=4):
    """
    Function to analyze every plane and get the result in a hdf5 file. It uses caiman_main
    Folder(str): folder where the input/output is/will be stored
    animal/day(str) to be analyzed
    num_files(int): number of files for the bmi file
    num_files_b(int): number of files for the baseline file
    number_planes(int): number of planes that carry information
    number_planes_total(int): number of planes given back by the recording system, it may differ from number_planes
    to provide time for the objective to return to origen
    dend(bool): Boleean to change parameters to look for neurons or dendrites
    display_images(bool): to display and save different plots"""

    folder_path = folder + 'raw/' + animal + '/' + day + '/separated/'
    finfo = folder + 'raw/' + animal + '/' + day + '/wmat.mat'  # file name of the mat
    matinfo = scipy.io.loadmat(finfo)
    fr = matinfo['fr'][0][0]
    snr_out = os.path.join(folder, 'raw', animal, day, 'SNR_{}_{}.hdf5'.format(animal, day))

    print('*************Starting with analysis*************')
    SNR_mats = []
    SNR_basemats = []
    planes = []
    fHats = []

    for plane in np.arange(number_planes):
        fnames = []
        for nf in np.arange(int(num_files_b)):
            fnames.append(folder_path + 'baseline' + '_plane_' + str(plane) + '_nf_' + str(nf) + '.tiff')
        print('performing plane: ' + str(plane))
        for nf in np.arange(int(num_files)):
            fnames.append(folder_path + 'bmi' + '_plane_' + str(plane) + '_nf_' + str(nf) + '.tiff')

        fpath = folder + 'raw/' + animal + '/' + day + '/analysis/' + str(plane) + '/'
        if not os.path.exists(fpath):
            os.makedirs(fpath)

        hf = h5py.File(folder + 'raw/' + animal + '/' + day + '/' + 'bmi_' +  '_' + str(plane) + '.hdf5', 'r')

        print(fnames)
        SNRcomps, SNR_comp_base, fHat = estimate_SNR_hfile(fnames, hf, fr)
        SNR_mats.append(SNRcomps)
        SNR_basemats.append(SNR_comp_base)
        fHats.append(fHat)
        planes = planes + len(SNRcomps) * [plane]
        hf.close()
        print('SNR done: saving ... plane: ' + str(plane))

    print('... done')
    try:
        with h5py.File(snr_out, 'w-') as snrhf:
            snrhf['SNR'] = np.concatenate(SNR_mats)
            snrhf['SNR_baseline'] = np.concatenate(SNR_basemats)
            snrhf['F_hat'] = np.vstack(fHats)
            snrhf['plane'] = planes
    except IOError:
        print(" OOPS!: The file already existed ease try with another file, new results will NOT be saved")


def all_run_SNR(folder, animal, day, number_planes=4, number_planes_total=6):
    """
    Function to run all the different functions of the pipeline that gives back the analyzed data
    Folder (str): folder where the input/output is/will be stored
    animal/day (str) to be analyzed
    number_planes (int): number of planes that carry information
    number_planes_total (int): number of planes given back by the recording system, it may differ from number_planes
    to provide time for the objective to return to origen"""

    folder_path = folder + 'raw/' + animal + '/' + day + '/'
    folder_final = folder + 'processed/' + animal + '/' + day + '/'
    err_file = open(folder_path + "errlog.txt", 'a+')  # ERROR HANDLING
    if not os.path.exists(folder_final):
        os.makedirs(folder_final)

    finfo = folder_path + 'wmat.mat'  # file name of the mat
    matinfo = scipy.io.loadmat(finfo)
    ffull = [folder_path + matinfo['fname'][0]]  # filename to be processed
    fbase = [folder_path + matinfo['fbase'][0]]

    try:
        wmatp = re.compile('wmat(.*).mat')
        bmip = re.compile('bmi_(.*).tif')
        bmi_count = len([1 for f in os.listdir(folder_path) if bmip.match(f)])
        wmat_count = len([1 for f in os.listdir(folder_path) if wmatp.match(f)])
        if wmat_count == 1:
            if bmi_count == 1:
                num_files, len_bmi = separate_planes(folder, animal, day, ffull, 'bmi', number_planes,
                                                     number_planes_total)
                num_files_b, len_base = separate_planes(folder, animal, day, fbase, 'baseline', number_planes,
                                                        number_planes_total)
            elif bmi_count == 2:
                fbase1 = [folder + 'raw/' + animal + '/' + day + '/' + 'baseline_00001.tif']
                fbase2 = [folder + 'raw/' + animal + '/' + day + '/' + 'bmi_00000.tif']
                num_files_b, len_base = separate_planes_multiple_baseline(folder, animal, day, fbase1,
                                                                               fbase2)
                num_files, len_bmi = separate_planes(folder, animal, day, ffull, 'bmi')
    except Exception as e:
        tb = sys.exc_info()[2]
        err_file.write("\n{}\n".format(folder_path))
        err_file.write("{}\n".format(str(e.args)))
        traceback.print_tb(tb, file=err_file)
        err_file.close()
        sys.exit('Error in separate planes')

    # nam = folder_path + 'readme.txt'
    # readme = open(nam, 'w+')
    # readme.write("num_files_b = " + str(num_files_b) + '; \n')
    # readme.write("num_files = " + str(num_files) + '; \n')
    # readme.write("len_base = " + str(len_base) + '; \n')
    # readme.write("len_bmi = " + str(len_bmi) + '; \n')
    # readme.close()

    try:
        calc_SNR_all_planes(folder, animal, day, num_files, num_files_b, number_planes)
    except Exception as e:
        tb = sys.exc_info()[2]
        err_file.write("\n{}\n".format(folder_path))
        err_file.write("{}\n".format(str(e.args)))
        traceback.print_tb(tb, file=err_file)
        err_file.close()
        sys.exit('Error in SNR calculation')

    try:
        shutil.rmtree(folder + 'raw/' + animal + '/' + day + '/separated/')
    except OSError as e:
        print("Error: %s - %s." % (e.filename, e.strerror))

    err_file.close()


#################################################################
#################### online SNR calculation #####################
#################################################################
def f0_filter_sig(xs, ys, method=2, width=30):
    """
    Return:
        dff: np.ndarray (T, 2)
            col0: dff
            col1: boundary scale for noise level
    """
    if method < 10:
        mf, mDC = median_filter(width, method)
    else:
        mf, mDC = std_filter(width, method%10, buffer=True)
    dff = np.array([(mf(ys, i), mDC.get_dev()) for i in range(len(ys))])
    return dff


def calcium_dff(xs, ys, method=2, width=30):
    f0 =f0_filter_sig(xs, ys, method=method, width=width)[:, 0]
    return (ys-f0) / f0


def caiman_SNR(xs, ys, source='raw', verbose=False):
    """
    method: str
        raw: raw caiman SNR
        dff: feed dff to caiman
        dbl: specify baseline in foopsi
        df: feed filtered signal to caiman
    """
    if source == 'raw':
        c, bl, c1, g, sn, sp, lam = constrained_foopsi(ys, p=2)
    elif source == 'dff':
        dff = calcium_dff(xs, ys)
        c, bl, c1, g, sn, sp, lam = constrained_foopsi(dff, p=2)
    elif source == 'dbl':
        bl0 = f0_filter_sig(xs, ys)[:, 0]
        c, bl, c1, g, sn, sp, lam = constrained_foopsi(ys, bl=bl0, p=2)
    elif source == 'df':
        c, bl, c1, g, sn, sp, lam = constrained_foopsi(ys-f0_filter_sig(xs, ys)[:, 0], p=2)
    else:
        raise NotImplementedError(f"source method {source} not recognized")
    sigpower = np.mean(np.square(c))
    snr = sigpower / (sn ** 2)
    if verbose:
        print(source, f'snr: {snr:.5f}', f'noise: {sn:.5f}', f'sigpower: {sigpower:.5f}',
              f'putative power: {(np.mean(np.square(ys-bl))-sn**2):.5f}')
    return snr


def online_SNR_single_session(folder, animal, day, out):
    # Calculates SNR for single session then saves it to 4 decimal accuracy and saves in hdf5 file
    dayfile = encode_to_filename(folder, animal, day)
    print(f'processing {dayfile}')
    outpath = os.path.join(out, animal, day)
    targetfile = os.path.join(outpath, f'onlineSNR_{animal}_{day}.hdf5')
    if os.path.exists(targetfile):
        print(f'{animal} {day} already done, skipping...')
    with h5py.File(dayfile, 'r') as session:
        if not os.path.exists(outpath):
            os.makedirs(outpath)
        Nens = session['online_data'].shape[1] - 2
        od = session['online_data']
        frame = np.array(od[:, 1]).astype(np.int32) // 6
        datamat = np.array(od[:, 2:])
        Tdf = frame[-1] + 1
        SNRs = []
        for i in range(Nens):
            data = datamat[:, i]
            sclean = ~np.isnan(data)
            f = interpolate.interp1d(frame[sclean], data[sclean], fill_value='extrapolate')
            all_online_frames = np.arange(Tdf)
            interp_online = f(all_online_frames)
            SNRs.append(caiman_SNR(all_online_frames, interp_online))
        try:
            with h5py.File(targetfile, 'w-') as osnr:
                osnr['SNR_ens'] = np.around(SNRs, 4)
        except IOError:
            print(" OOPS!: The file already existed please try with another file, "
                  "new results will NOT be saved")


def dff_SNR_single_session(folder, animal, day, out):
    # Calculates SNR for single session then saves it to 4 decimal accuracy and saves in hdf5 file
    dayfile = encode_to_filename(folder, animal, day)
    print(f'processing {dayfile}')
    outpath = os.path.join(out, animal, day)
    targetfile = os.path.join(outpath, f'dffSNR_{animal}_{day}.hdf5')
    if os.path.exists(targetfile):
        print(f'{animal} {day} already done, skipping...')
    with h5py.File(dayfile, 'r') as session:
        if not os.path.exists(outpath):
            os.makedirs(outpath)
        dff = np.array(session['dff'])
        Nneur = dff.shape[0]
        T = dff.shape[1]
        frame = np.arange(T)
        SNRs = np.empty(Nneur, dtype=np.float64)
        for i in range(Nneur):
            data = dff[i, :]
            sclean = ~np.isnan(data)
            if sum(sclean) != T:
                f = interpolate.interp1d(frame[sclean], data[sclean], fill_value='extrapolate')
                trace = f(frame)
            else:
                trace = data
            SNRs[i] = caiman_SNR(frame, trace)
        try:
            with h5py.File(targetfile, 'w-') as osnr:
                osnr['SNR_ens'] = np.around(SNRs, 4)
        except IOError:
            print(" OOPS!: The file already existed please try with another file, "
                  "new results will NOT be saved")


if __name__ == '__main__':
    home = "/home/user/"
    #processed = os.path.join(home, "CaBMI_analysis/processed/")
    processed = '/Volumes/DATA_01/NL/layerproject/processed'
    out = '/Users/albertqu/Documents/7.Research/BMI/analysis_data'
    coactivation_single_session((processed, 'IT2', '181003'), window=3000, include_dend=False, source='dff',
                                out=out)
    #raw_activity_tuning(home, {'IT': {'IT2': '*'}, 'PT': {'PT19': "*"}}, nproc=4)
    #raw_activity_tuning(home, '*', nproc=4)
    #C_activity_tuning(home, {'IT':{'IT2': ['181002', '181003']}})
