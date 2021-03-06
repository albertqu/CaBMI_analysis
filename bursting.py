import numpy as np
import json
import seaborn as sns
import pandas as pd
import os, h5py
from utils_bursting import *
from plotting_functions import best_nbins
from utils_loading import get_PTIT_over_days, path_prefix_free, file_folder_path,\
    decode_from_filename, encode_to_filename, get_redlabel, parse_group_dict, decode_method_ibi
from utils_cabmi import time_lock_activity
import matplotlib.pyplot as plt
from scipy import io
from preprocessing import get_peak_times_over_thres
from matplotlib.widgets import Slider


def calcium_IBI_single_session_windows(inputs, out, window=None, perc=30, ptp=True):
    """Returns a metric matrix and meta data of IBI metric
    Params:
        inputs: str, h5py.File, tuple, or np.ndarray
            if str/h5py.File: string that represents the filename of hdf5 file
            if tuple: (path, animal, day), that describes the file location
            if np.ndarray: array C of calcium traces
        out: str
            Output path for saving the metrics in a hdf5 file
            outfile: h5py.File
                N: number of neurons
                s: number of sliding sessions
                K: maximum number of IBIs extracted
                'mean': N * s matrix, means of IBIs
                'stds': N * s matrix, stds of IBIs
                'CVs': N * s matrix, CVs of IBIs
                'IBIs': N * s * K, IBIs
        window: None or int
            sliding window for calculating IBIs.
            if None, use 'blen' in hdf5 file instead, but inputs have to be str/h5py.File
        perc: float
            hyperparameter for partitioning algorithm, correlated with tail length of splitted calcium trace
        ptp: boolean
            True if IBI is based on peak to peak measurement, otherwise tail to tail

    Alternatively, could store data in:
        mat_ibi: np.ndarray
            N * s * m matrix, , where N is the number of neurons, s is number of sliding sessions,
            m is the number of metrics
        meta: dictionary
            meta data of form {axis: labels}
    """
    if isinstance(inputs, np.ndarray):
        C = inputs
        window = C.shape[1]
        animal, day = None, None
    else:
        if isinstance(inputs, str):
            opts = path_prefix_free(inputs, '/').split('_')
            animal, day = opts[1], opts[2]
            f = h5py.File(inputs, 'r')
        elif isinstance(inputs, h5py.File):
            opts = path_prefix_free(inputs.filename, '/').split('_')
            animal, day = opts[1], opts[2]
            f = inputs
        elif isinstance(inputs, tuple):
            path, animal, day = inputs
            hfile = os.path.join(path, animal, day, "full_{}_{}__data.hdf5".format(animal, day))
            f = h5py.File(hfile, 'r')
        else:
            raise RuntimeError("Input Format Unknown!")
        C = np.array(f['C'])
        if window is None:
            window0 = window
            window = f.attrs['blen']
        f.close()
    if animal is None:
        savepath = os.path.join(out, 'sample_IBI.hdf5')
    else:
        hyperparams = 'theta_perc{}{}_window{}'.format(perc, '_ptp' if ptp else "", window0)
        savepath = os.path.join(out, animal, day)
        if not os.path.exists(savepath):
            os.makedirs(savepath)
        savepath = os.path.join(savepath, "IBI_{}_{}_{}.hdf5".format(animal, day, hyperparams))
    if os.path.exists(savepath):
        with h5py.File(savepath, 'r') as f:
            N, nsessions = f['mean'].shape[:2]
        return savepath, N, nsessions
    nsessions = int(np.ceil(C.shape[1] / window))
    rawibis = {}
    maxLen = -1
    for i in range(C.shape[0]):
        rawibis[i] = {}
        for s in range(nsessions):
            slide = C[i, s*window:min(C.shape[1], (s+1) * window)]
            ibis = neuron_calcium_ipri(slide, perc, ptp)
            rawibis[i][s] = ibis
            maxLen = max(len(ibis), maxLen)

    all_ibis = np.full((C.shape[0], nsessions, maxLen), np.nan)
    for i in range(C.shape[0]):
        for s in range(nsessions):
            all_ibis[i][s][:len(rawibis[i][s])] = rawibis[i][s]
    means = np.nanmean(all_ibis, axis=2)
    stds = np.nanstd(all_ibis, axis=2)
    cvs = stds / means
    outfile = h5py.File(savepath, 'w-')
    outfile['mean'], outfile['stds'], outfile['CVs'] = means, stds, cvs
    outfile['IBIs'] = all_ibis
    outname = outfile.filename
    outfile.close()
    return outname, C.shape[0], nsessions
    #return np.concatenate([means, stds, cvs], axis=2), {2: ['mean', 'stds', 'CVs']}


def calcium_IBI_all_sessions_windows(folder, window=None, perc=30, ptp=True, IBI_dist=False):
    """Returns a metric matrix across all sessions and meta data of IBI metric
        Params:
            folder: str
                root folder path where all the processed hdf5 will be stored
            out: str
                Output path for saving the metrics in a hdf5 file
                outfile: h5py.File
                    N: number of neurons
                    s: number of sliding sessions
                    K: maximum number of IBIs extracted
                    'mean': N * s matrix, means of IBIs
                    'stds': N * s matrix, stds of IBIs
                    'CVs': N * s matrix, CVs of IBIs
                    'IBIs': N * s * K, IBIs
                All stored by animal/day/IBI_animal_day_hyperparams.hdf5
            window: None or int
                sliding window for calculating IBIs.
                if None, use 'blen' in hdf5 file instead, but inputs have to be str/h5py.File
            perc: float
                hyperparameter for partitioning algorithm, correlated with tail length of splitted calcium trace
            ptp: boolean
                True if IBI is based on peak to peak measurement, otherwise tail to tail
            IBI_dist: boolean
                generate the IBI_distribution matrix if True

        Returns:
            mats: {group: {mat_ibi, (mat_ibi_dist,) meta}}
            mat_ibi: np.ndarray (first 4 ~ 8.93MB)
                A * D * N * s * m matrix,
                A: number of animals
                D: number of days
                N: number of neurons
                s: number of sliding sessions,
                m is the number of metrics

            meta: dictionary
                meta data of form {group: {axis: labels}}

        IO:
            summary.mat: dict
                {group: (A, D, N, s, nibis)}, first four the dimension of the ibi metric matrix,
                nibis is the maximum number of ibis
        """
    processed = os.path.join(folder, 'CaBMI_analysis/processed')
    out = os.path.join(folder, 'bursting/IBI')
    if 'navigation.json' in os.listdir(processed):
        with open(os.path.join(processed, 'navigation.json'), 'r') as jf:
            all_files = json.load(jf)
    else:
        all_files = get_PTIT_over_days(processed)
    calculate = True
    summary_file = os.path.join(out, 'summary.json')
    summary_mat = {}
    if os.path.exists(summary_file):
        with open(summary_file, 'r') as jf:
            summary_mat = json.load(jf)
        calculate = False
    mats = {}
    hyperparam = 'theta_perc{}{}_window{}'.format(perc, '_ptp' if ptp else "", window)
    for group in 'IT', 'PT':
        animal_map = all_files[group]['maps']
        mats[group] = {'meta': [''] * len(animal_map)}
        if calculate:
            summary_mat[group] = [len(animal_map), len(all_files[group]) - 1] + [0] * 3
            temp = {}
        else:
            mats[group]['mat_ibi'] = np.full(tuple(summary_mat[group][:4]) + (3,), np.nan)
            if IBI_dist:
                mats[group]['mat_ibi_dist'] = np.full(summary_mat[group], np.nan)
        for d in all_files[group]:
            if d == 'maps':
                continue
            animal_files = all_files[group][d]
            if calculate:
                temp[d] = {}
            for filename in animal_files:
                print(filename)
                animal, day = decode_from_filename(filename)
                if calculate:
                    burst_file = calcium_IBI_single_session((processed, animal, day),
                                            out, window=window, perc=perc, ptp=ptp)[0]
                else:
                    burst_file = os.path.join(out, animal, day,
                                              encode_to_filename(out, animal, day, hyperparam))
                #try:
                burst_data = h5py.File(burst_file, 'r')
                metrics = np.stack((burst_data['mean'], burst_data['stds'], burst_data['CVs']), axis=-1)
                animal_ind = animal_map[animal]
                mats[group]['redlabels'][animal_ind, int(d)-1] = get_redlabel(processed, animal, day)
                if calculate:
                    temp[d][animal] = {'mat_ibi': metrics}
                    if IBI_dist:
                        temp[d][animal]['mat_ibi_dist'] = burst_data['IBIs']
                    summary_mat[group][2] = max(metrics.shape[0], summary_mat[group][2])
                    summary_mat[group][3] = max(metrics.shape[1], summary_mat[group][3])
                    summary_mat[group][4] = max(burst_data['IBIs'].shape[-1], summary_mat[group][4])
                else:
                    temp = {'mat_ibi': metrics}
                    if IBI_dist:
                        temp['mat_ibi_dist'] = burst_data['IBIs']
                    for opt in mats[group]:
                        if opt == 'meta':
                            continue
                        animal_ind = animal_map[animal]
                        tN, ts, tm = temp[opt].shape
                        mats[group][opt][animal_ind, int(d) - 1, :tN, :ts, :tm] = temp[opt]
                        mats[group]['meta'][animal_ind] = animal
                # except Exception as e:
                #     skipped.append(e.args)
                #     print(e.args)
        #try:
        summary_mat[group] = tuple(summary_mat[group])
        if calculate:
            mats[group]['mat_ibi'] = np.full(summary_mat[group][:4] + (3,), np.nan)
            mats[group]['redlabels'] = np.full(summary_mat[group][:3], False)
            if IBI_dist:
                mats[group]['mat_ibi_dist'] = np.full(summary_mat[group], np.nan)
            for opt in mats[group]:
                if opt == 'meta':
                    continue
                for d in temp:
                    for animal in temp[d]:
                        animal_ind = animal_map[animal]
                        tN, ts, tm = temp[d][animal][opt].shape
                        mats[group][opt][animal_ind, int(d)-1, :tN, :ts, :tm] = temp[d][animal][opt]
                        mats[group]['meta'][animal_ind] = animal
        """except Exception as e:
            skipped.append(str(e.args))
            print(e.args)"""
    if calculate:
        with open(summary_file, 'w') as jf:
            json.dump(summary_mat, jf)
    """f = open(os.path.join(folder, 'errLOG.txt'), 'w')
    f.write("\n".join([str(s) for s in skipped]))
    f.close()"""
    return mats


def calcium_IBI_single_session(inputs, out, window=None, method=0, peak_csv=True):
    """Returns a metric matrix and meta data of IBI metric
    Params:
        inputs: str, h5py.File, tuple, or np.ndarray
            if str/h5py.File: string that represents the filename of hdf5 file
            if tuple: (path, animal, day), that describes the file location
            if np.ndarray: array C of calcium traces
        out (I/O): str
            Output path for saving the metrics in a hdf5 file
            outfile: h5py.File
                N: number of neurons
                s: number of sliding sections
                t: number of trials
                K: maximum number of IBIs extracted
                K': maximum number of IBIs within each trial
                'IBIs_window': N * s * K, IBIs across window
                'IBIs_trial': N * t * K', IBIs across trial
        window: None or int
            sliding window for calculating IBIs.
            if None, use 'blen' in hdf5 file instead, but inputs have to be str/h5py.File
        method: int/float
            if negative:
                Use signal_partition algorithm in shuffling_functions.py, the absolute value is the perc
                parameter
                perc: float
                    hyperparameter for partitioning algorithm, correlated with tail length of splitted calcium trace
                if method < -100:
                    ptp = False
                    ptp: boolean
                        True if IBI is based on peak to peak measurement, otherwise tail to tail
            Else:
                opt, thres = method // 10, method % 10
                opt: 0: std
                     1: mad
                thres: number of std/mad
    ***********************************************************************************************
     Alternatively, could store data in:
        mat_ibi: np.ndarray
            N * s * m matrix, , where N is the number of neurons, s is number of sliding sessions,
            m is the number of metrics
        meta: dictionary
            meta data of form {axis: labels}
    ***********************************************************************************************
    """
    if method == 0:
        return [calcium_IBI_single_session(inputs, out, window, m) for m in (1, 2, 11, 12)]
    if isinstance(inputs, np.ndarray):
        C = inputs
        t_locks = None
        window = C.shape[1]
        animal, day = None, None
    else:
        f = None
        if isinstance(inputs, str):
            opts = path_prefix_free(inputs, '/').split('_')
            animal, day = opts[1], opts[2]
            hfile = inputs
        elif isinstance(inputs, h5py.File):
            opts = path_prefix_free(inputs.filename, '/').split('_')
            animal, day = opts[1], opts[2]
            hfile = inputs.filename
            f = inputs
        elif isinstance(inputs, tuple):
            path, animal, day = inputs
            hfile = os.path.join(path, animal, day, "full_{}_{}__data.hdf5".format(animal, day))
        else:
            raise RuntimeError("Input Format Unknown!")
        if f is None:
            f = h5py.File(hfile, 'r')
        C = np.array(f['C'])
        if peak_csv:
            if window is None:
                window0 = window
                window = f.attrs['blen']
            D_trial, D_window = get_peak_times_over_thres(hfile, window, method)
        else:
            t_locks = time_lock_activity(f, order='N')
            if window is None:
                window0 = window
                window = f.attrs['blen']
            else:
                window0 = window
            f.close()
    nsessions = int(np.ceil(C.shape[1] / window))
    ibi_func, hp = decode_method_ibi(method)
    if animal is None:
        savepath = os.path.join(out, 'sample_IBI.hdf5')
    else:
        hyperparams = 'theta_{}_window{}'.format(hp, window0)
        savepath = os.path.join(out, animal, day)
        if not os.path.exists(savepath):
            os.makedirs(savepath)
        savepath = os.path.join(savepath, "IBI_{}_{}_{}.hdf5".format(animal, day, hyperparams))
    if os.path.exists(savepath):
        with h5py.File(savepath, 'r') as f:
            N, nsessions = f['mean'].shape[:2]
        print("Existed, ", animal, day)
        return savepath, N, nsessions
    if peak_csv:
        all_ibis_windows, all_ibis_trials = dict_to_mat(D_window), dict_to_mat(D_trial)
    else:
        print("Starting IBI calculation, ", animal, day)
        rawibis_windows = {}
        maxLenW = -1
        if t_locks is not None:
            rawibis_trials = {}
            maxLenT = -1
        for i in range(C.shape[0]):
            print(i)
            rawibis_windows[i] = {}
            for s in range(nsessions):
                slide = C[i, s*window:min(C.shape[1], (s+1) * window)]
                ibis = ibi_func(slide)
                rawibis_windows[i][s] = ibis
                maxLenW = max(len(ibis), maxLenW)
            if t_locks is not None:
                rawibis_trials[i] = {}  # TODO: Modify IBIs to handle empty trials
                for s in range(t_locks.shape[1]):
                    slide = t_locks[i, s]
                    ibis = ibi_func(slide)
                    rawibis_trials[i][s] = ibis
                    maxLenT = max(len(ibis), maxLenT)

        all_ibis_windows = np.full((C.shape[0], nsessions, maxLenW), np.nan)
        if t_locks is not None:
            all_ibis_trials = np.full((C.shape[0], t_locks.shape[1], maxLenT), np.nan)
        for i in range(C.shape[0]):
            for s in range(nsessions):
                all_ibis_windows[i][s][:len(rawibis_windows[i][s])] = rawibis_windows[i][s]
            if t_locks is not None:
                for s in range(t_locks.shape[1]):
                    all_ibis_trials[i][s][:len(rawibis_trials[i][s])] = rawibis_trials[i][s]
    outfile = h5py.File(savepath, 'w-')
    outfile['IBIs_window'] = all_ibis_windows
    outfile['IBIs_trial'] = all_ibis_trials
    outname = outfile.filename
    outfile.close()
    return outname, C.shape[0], nsessions


def calcium_IBI_all_sessions(folder, groups, window=None, method=0, options=('window', 'trial'),
                             peak_csv=True):
    # TODO: ADD OPTION TO PASS IN A LIST OF METHODS FOR COMPARING THE PLOTS!
    """Returns a metric matrix across all sessions and meta data of IBI metric
        Params:
            folder: str
                root folder path where all the processed hdf5 will be stored
            out: str
                Output path for saving the metrics in a hdf5 file
                outfile: h5py.File
                A: number of animals
                D: number of days
                N: number of neurons
                s: number of sliding sections
                t: number of trials
                K: maximum number of IBIs extracted
                K': maximum number of IBIs within each trial
                All stored by animal/day/IBI_animal_day_hyperparams.hdf5
            window: None or int
                sliding window for calculating IBIs.
                if None, use 'blen' in hdf5 file instead, but inputs have to be str/h5py.File
            perc: float
                hyperparameter for partitioning algorithm, correlated with tail length of splitted calcium trace
            ptp: boolean
                True if IBI is based on peak to peak measurement, otherwise tail to tail
            IBI_dist: boolean
                generate the IBI_distribution matrix if True

        Returns:
            res_mat: dict
                IBIs_window
                IBIs_trial
                redlabel
                array_t1
                array_miss

            mats: {group: {mat_ibi, (mat_ibi_dist,) meta}}
            mat_ibi_window: np.ndarray (first 4 ~ 8.93MB)
                A * D * N * s * m matrix,
                A: number of animals
                D: number of days
                N: number of neurons
                s: number of windows,
                K: the number of metrics
            mat_ibi_trial: np.ndarray (first 4 ~ 8.93MB)
                A * D * N * s * m matrix,
                A: number of animals
                D: number of days
                N: number of neurons
                t: number of trials,
                m: the number of metrics

            meta: dictionary
                meta data of form {group: {axis: labels}}
        """
    if method == 0:
        return {m: calcium_IBI_all_sessions(folder, groups, window, m) for m in (1, 2, 11, 12)}
    processed = os.path.join(folder, 'CaBMI_analysis/processed')
    out = os.path.join(folder, 'bursting/IBI')
    if groups == '*':
        all_files = get_PTIT_over_days(processed)
    else:
        all_files = {g: parse_group_dict(processed, groups[g], g) for g in groups.keys()}
    print(all_files)
    hyperparam = 'theta_{}_window{}'.format(decode_method_ibi(method)[1], window)
    mats = {'meta': hyperparam}
    skipper=open("../skipperB.txt", 'a+')
    for group in all_files:
        group_dict = all_files[group]
        maxA, maxD, maxN = len(group_dict), max([len(group_dict[a]) for a in group_dict]), 0
        temp = {}
        res_mat = {"IBIs_{}".format(o): [0, 0] for o in options} # maxW/T, maxK
        skipped = {}
        for animal in group_dict:
            temp[animal] = {}
            for day in sorted(group_dict[animal]):
                hf = encode_to_filename(processed, animal, day)
                hf_burst = encode_to_filename(out, animal, day, hyperparams=hyperparam)
                errorFile = False
                if not os.path.exists(hf_burst):
                    try:
                        calcium_IBI_single_session(hf, out, window, method)
                        print('Finished', animal, day)
                    except Exception as e:
                        errorFile = True
                        if animal in skipped:
                            skipped[animal].append([day])
                        else:
                            skipped[animal] = [day]
                        skipper.write(animal+', '+day)
                if not peak_csv:
                    if not errorFile:
                        temp[animal][day] = {}
                        with h5py.File(hf, 'r') as f:
                            temp[animal][day]['redlabel'] = np.array(f['redlabel'])
                            if 'trial' in options:
                                array_t1, array_miss = np.array(f['array_t1']), np.array(f['array_miss'])
                                a_t1, a_miss = np.full(len(f['trial_start']), False), np.full(len(f['trial_start']), False)
                                a_t1[array_t1] = True
                                a_miss[array_miss] = True
                                temp[animal][day]['array_t1'] = a_t1
                                temp[animal][day]['array_miss'] = a_miss
                        
                        with h5py.File(hf_burst, 'r') as f:
                            for i, o in enumerate(options):
                                arg = 'IBIs_{}'.format(o)
                                ibi = f[arg]
                                if i == 0:
                                    maxN = max(ibi.shape[0], maxN)
                                
                                temp[animal][day][o] = np.array(ibi)
                                res_mat[arg][0] = max(ibi.shape[1], res_mat[arg][0])
                                res_mat[arg][1] = max(ibi.shape[-1], res_mat[arg][1])
        if not peak_csv:
            maxA, maxD = len(temp), len(temp[max(temp.keys(), key=lambda k: len(temp[k]))])
            animal_maps = {}
            for k in res_mat:
                maxS, maxK = res_mat[k][0], res_mat[k][1]
                res_mat[k] = np.full((maxA, maxD, maxN, maxS, maxK), np.nan)
            res_mat['redlabel'] = np.full((maxA, maxD, maxN), False)
            if 'trial' in options:
                res_mat['array_t1'] = np.full((maxA, maxD, maxN, res_mat['IBIs_trial'].shape[-2]), False)
                res_mat['array_miss'] = np.full((maxA, maxD, maxN, res_mat['IBIs_trial'].shape[-2]), False)
            for i, animal in enumerate(temp):
                animal_maps[i] = animal
                for j, d in enumerate(sorted([k for k in temp[animal].keys()])):
                    res_mat['redlabel'][i, j,:len(temp[animal][d]['redlabel'])] = temp[animal][d]['redlabel']
                    del temp[animal][d]['redlabel']
                    if 'trial' in options:
                        at1 = temp[animal][d]['array_t1']
                        am1 = temp[animal][d]['array_miss']
                        res_mat['array_t1'][i, j, :, :len(at1)] = at1
                        del temp[animal][d]['array_t1']
                        del temp[animal][d]['array_miss']
                        res_mat['array_miss'][i, j, :, :len(am1)] = am1
                    for o in options:
                        tIBI = temp[animal][d][o]
                        res_mat['IBIs_{}'.format(o)][i, j, :tIBI.shape[0], :tIBI.shape[1], :tIBI.shape[2]] = tIBI
                        del temp[animal][d][o]
            res_mat['animal_map'] = animal_maps
            mats[group] = res_mat
    skipper.close()
    return mats


def IBI_to_metric_save(folder, processed, animals=None, window=None, method=0, test=True):
    # TODO: add asymtotic learning rate as well
    """Returns pandas DataFrame object consisting all the experiments
    Params:
        folder: str
            Input directory
        method: int
            threshold method for peak detection
        in (I/O): each ANIMAL/DAY in folder
            ibif: hdf5.File
            Contents
                N: number of neurons
                s: number of sliding sections
                t: number of trials
                K: maximum number of IBIs extracted
                K': maximum number of IBIs within each trial
                'IBIs_window': N * s * K, IBIs across window
                'IBIs_trial': N * t * K', IBIs across trial
    Returns:
        out (I/O):
            all_df_window: pd.DataFrame
                cols: [group|animal|date|session|roi_type|window|N|cv|cv_ub|serr_pc]
            all_df_trial: pd.DataFrame
                cols: [group|animal|date|session|trial|HM_trial|N|roi_type|cv|cv_ub|serr_pc]
        """
    # TODO: ALLOCATE MEMORY Posteriorly
    hp = 'theta_{}_window{}'.format(decode_method_ibi(method)[1], window)
    if method == 0:
        return {m: IBI_to_metric_save(folder, m) for m in (1, 2, 11, 12)}
    if animals is None:
        animals = os.listdir(folder)
        meta = ""
    else:
        meta = "_" + "_".join(animals)
    # for animal in os.listdir(folder):
    trial_target = os.path.join(folder, 'df_trial{}_{}.csv'.format(meta, hp))
    window_target = os.path.join(folder, 'df_window{}_{}.csv'.format(meta, hp))

    if test and os.path.exists(trial_target) and os.path.exists(window_target):
        all_df_trial, all_df_window = pd.read_csv(trial_target), pd.read_csv(window_target)

    else:
        all_df_trial, all_df_window = pd.DataFrame(), pd.DataFrame() #TODO: think of ways to speed up
        skipper = open(os.path.join(folder, "skipper.txt"), 'w')
        for animal in animals:
            if animal.startswith('PT') or animal.startswith('IT'):
                for i, day in enumerate(sorted([d for d in os.listdir(os.path.join(processed, animal))
                                                if d.isnumeric()])):
                    hf = encode_to_filename(folder, animal, day, hp)
                    if not os.path.exists(hf):
                        print("Skipping, ", hf)
                        skipper.write(hf + "\n")
                        continue
                    print(animal, day)
                    df_window, df_trial = IBI_to_metric_single_session(hf, processed, test=test)
                    df_window.loc[:, 'group'] = animal[:2]
                    df_trial.loc[:, 'group'] = animal[:2]
                    df_window.loc[:, 'animal'] = animal
                    df_trial.loc[:, 'animal'] = animal
                    df_window.loc[:, 'date'] = day # Real Date
                    df_trial.loc[:, 'date'] = day
                    df_window.loc[:, 'session'] = i + 1
                    df_trial.loc[:, 'session'] = i + 1
                    all_df_window = all_df_window.append(df_window)
                    all_df_trial = all_df_trial.append(df_trial)
        print('Done with all loops')
        all_df_trial.loc[:, 'HIT/MISS'] = 'miss'
        all_df_trial.loc[all_df_trial['HM_trial'] > 0, 'HIT/MISS'] = 'hit'
        all_df_trial.loc[:, 'HM_trial'] = np.abs(all_df_trial.loc[:, 'HM_trial'])
        if test:
            print('Start Saving')
            all_df_trial.to_csv(trial_target, index=False)
            all_df_window.to_csv(window_target, index=False)
        skipper.close()
    return {'window': all_df_window, 'trial': all_df_trial, 'meta': meta}


def IBI_to_metric_single_session(inputs, processed, test=True):
    # TODO: add asymtotic learning rate as well
    """Returns a pd.DataFrame with peak timing for calcium events
        Params:
            inputs: str, h5py.File
                string that represents the filename of hdf5 file
                Contents:
                    N: number of neurons
                    s: number of sliding sections
                    t: number of trials
                    K: maximum number of IBIs extracted
                    K': maximum number of IBIs within each trial
                    'IBIs_window': N * s * K, IBIs across window
                    'IBIs_trial': N * t * K', IBIs across trial
            out (I/O):
                df_window: pd.DataFrame
                    cols: [roi_type|window|N|cv|cv_ub|serr_pc]
                df_trial: pd.DataFrame
                    cols: [trial|HM_trial|N|roi_type|cv|cv_ub|serr_pc]
    """
    if isinstance(inputs, str):
        opts = path_prefix_free(inputs, '/').split('_')
        path = file_folder_path(inputs)
        animal, day = opts[1], opts[2]
        f = h5py.File(inputs, 'r')
        fname = inputs
    elif isinstance(inputs, h5py.File):
        opts = path_prefix_free(inputs.filename, '/').split('_')
        path = file_folder_path(inputs.filename)
        animal, day = opts[1], opts[2]
        f = inputs
        fname = inputs.filename
    else:
        raise RuntimeError("Input Format Unknown!")
    wcsv = os.path.join(path,'{}_{}_window_test.csv'.format(animal, day))
    tcsv = os.path.join(path,'{}_{}_trial_test.csv'.format(animal, day))
    if test and os.path.exists(wcsv) and os.path.exists(tcsv):
        return pd.read_csv(wcsv), pd.read_csv(tcsv)
    if 'df_window' in f and 'df_trial' in f and not test:
        df_window, df_trial = pd.read_hdf(fname, 'df_window'), pd.read_hdf(fname, 'df_trial')
        if len(df_window[df_window['roi_type'] == 'E2']) == 0:
            with h5py.File(encode_to_filename(processed, animal, day), 'r') as fp:
                if 'e2_neur' in fp:
                    ens_neur = np.array(fp['ens_neur'])
                    e2_neur = ens_neur[fp['e2_neur']]
                    for e in e2_neur:
                        df_window.loc[df_window['N'] == e, 'roi_type'] = 'E2'
                        df_trial.loc[df_trial['N'] == e, 'roi_type'] = 'E2'
                    df_window.to_hdf(fname, 'df_window')
                    df_trial.to_hdf(fname, 'df_trial')
        f.close()
        return df_window, df_trial
    fp = h5py.File(encode_to_filename(processed, animal, day), 'r')
    array_hit, array_miss = np.array(fp['array_t1']), np.array(fp['array_miss'])
    ens_neur = np.array(fp['ens_neur'])
    e2_neur = ens_neur[fp['e2_neur']] if 'e2_neur' in fp else None
    redlabel, nerden = np.array(fp['redlabel']), np.array(fp['nerden'])
    mets_window, mets_trial = IBI_cv_matrix(np.array(f['IBIs_window']), metric='all'), \
                              IBI_cv_matrix(np.array(f['IBIs_trial']),  metric='all')
    f.close()
    fp.close()

    resW, resT = {}, {}
    # Ensemble Neuron Possibly Unlabeled
    probeW, probeT = mets_window['cv'], mets_trial['cv']
    assert probeW.shape[0] == probeT.shape[0], 'Inconsistent shape between windows and trials measures!'
    N, sw, st = probeW.shape[0], probeW.shape[1], probeT.shape[1]
    rois = np.full(N, "D", dtype="U2")
    rois[nerden & ~redlabel] = 'IG'
    rois[nerden & redlabel] = 'IR'
    if e2_neur is not None:
        rois[ens_neur] = 'E1'
        rois[e2_neur] = 'E2'
    else:
        rois[ens_neur] = 'E'
    # DF TRIAL
    resW['window'] = np.tile(np.arange(sw), N)
    resW['roi_type'] = np.repeat(rois, sw)
    resW['N'] = np.repeat(np.arange(N), sw)
    # DF TRIAL
    trials = np.arange(1, st+1)
    tempm = trials[array_miss]
    temph = trials[array_hit]
    misses = np.empty_like(tempm)
    hits = np.empty_like(temph)
    sortedm = np.argsort(tempm)
    sortedh = np.argsort(temph)
    for i in range(len(sortedm)):
        misses[sortedm[i]] = -i-1
    for i in range(len(sortedh)):
        hits[sortedh[i]] = i+1
    hm_trial = np.empty_like(trials)
    hm_trial[array_hit] = hits
    hm_trial[array_miss] = misses
    #trials[array_miss] = -trials[array_miss]
    # awhere = np.where(trials < 0)[0]
    # assert np.array_equal(awhere, array_miss), "NOt alligned {} {}".format(awhere, array_miss)
    resT['trial'] = np.tile(trials, N)  # 1-indexed
    resT['HM_trial'] = np.tile(hm_trial, N) # 1-indexed
    resT['roi_type'] = np.repeat(rois, st)
    resT['N'] = np.repeat(np.arange(N), st)
    for k in mets_window:
        resW[k] = mets_window[k].ravel(order='C')
        resT[k] = mets_trial[k].ravel(order='C')
    print('Generating hdf')
    df_window = pd.DataFrame(resW)
    #print(N, sw, st)
    # def debug_print(res):
    #     for k in res.keys():
    #         print(k, res[k].shape)
    # debug_print(resW)
    # debug_print(resT)
    df_trial = pd.DataFrame(resT)
    if test:
        # testing = os.path.join(path, 'test.csv')
        # if os.path.exists(testing):
        #     print('Deleting', testing)
        #     os.remove(testing)
        df_window.to_csv(os.path.join(path, '{}_{}_window_test.csv'.format(animal, day)), index=False)
        df_trial.to_csv(os.path.join(path, '{}_{}_trial_test.csv'.format(animal, day)), index=False)
    else:
        df_window.to_hdf(fname, 'df_window')
        df_trial.to_hdf(fname, 'df_trial')

    return df_window, df_trial
    #
    # if method == 0:
    #     return {m: IBI_to_metric_save(folder, m) for m in (1, 2, 11, 12)}
    # dict_trial = {l: [] for l in ('group', 'animal', 'day', 'neuron', 'roi', 'HM', 'trial', 'CV',
    #                               'CV_unbiased', 'StdErr_percent')}
    # dict_window = {l: [] for l in ('group', 'animal', 'day', 'neuron', 'roi', 'window', 'CV',
    #                                'CV_unbiased', 'StdErr_percent')}
    # for animal in os.listdir(folder):
    #     if animal.startswith('PT') or animal.startswith('IT'):
    #         for day in os.listdir(os.path.join(folder, animal)):
    #             if day.isnumeric():
    #                 daypath = os.path.join(folder, animal, day)
    #                 ibif = h5py.File(os.path.join(daypath,
    #                                               encode_to_filename(folder, animal, day,
    #                                                                  decode_method_ibi(method))))
    #                 f = IBI_cv_matrix(ibif['IBIs_window'], metric='all')


def IBI_to_metric_window(ibi_mat, metric='cv', mask=True):
    """Returns metric mats for IBIs_window"""
    res = {}
    for group in ibi_mat:
        if group != 'meta':
            res[group] = {}
            k = 'IBIs_window'
            if mask:
                ibis = ibi_mat[group][k][ibi_mat[group]['redlabel']]
            else:
                ibis = ibi_mat[group][k]
                res[group]['redlabel'] = ibi_mat[group]['redlabel']
            res[group][k] = IBI_cv_matrix(ibis, metric)
    res['meta'] = ibi_mat['meta'] + '_m_{}'.format(metric)
    return res


def IBI_to_metric_trial(ibi_mat, metric='cv', mask=True):
    """ Returns metric mats for IBIs_trial
    """
    # TODO: 1. ADD procedure to effciently computer all trials as well, if "trial" is
    #  needed for evolution analysis
    #  2. ADD procedure to handle binning of multiple trials, sofar n_trials=1

    res = {}
    for group in ibi_mat:
        if group != 'meta':
            res[group] = {}
            k = 'IBIs_trial'
            hit_mask = ibi_mat[group]['array_t1']
            miss_mask = ibi_mat[group]['array_miss']
            redmask = ibi_mat[group]['redlabel'][:, :, :, np.newaxis]
            if mask:
                hit_mask = np.logical_and(redmask, hit_mask)
                miss_mask = np.logical_and(redmask, miss_mask)
                ibis_hits = ibi_mat[group][k][hit_mask]
                ibis_misses = ibi_mat[group][k][miss_mask]
                res[group]['IBIs_hit'] = IBI_cv_matrix(ibis_hits, metric)
                res[group]['IBIs_miss'] = IBI_cv_matrix(ibis_misses, metric)
            else:
                res[group]['redlabel'] = redmask
                res[group]['array_hit'] = hit_mask
                res[group]['array_miss'] = miss_mask
                res[group][k] = IBI_cv_matrix(ibi_mat[group][k], metric)
    res['meta'] = ibi_mat['meta'] + '_m_{}'.format(metric)
    return res


def displot_comp():
    p1 = sns.color_palette("Blues", n_colors=7)
    p2 = sns.color_palette("Reds", n_colors=7)
    def sinplot(p, l, flip=1):
        x = np.linspace(0, 14, 100)
        for i in range(1, 7):
            plt.plot(x, np.sin(x + i * .5) * (7 - i) * flip, color=p[i], label=l)
    sinplot(p1, 'IT', 1)
    sinplot(p2, 'PT', -1)
    plt.legend()
    plt.show()


def plot_IBI_ITPT_contrast_all_sessions(metric_mats, out, metric='all', from_csv=True, bins=None,
                                        same_rank=True, eps=True, eigen=True):
    """ Takes in DataFrame of trials or windows or both, and save plots in out directory

    Params:
        metric_mats: dict
            {['window': df_window], ['trial': df_trial]}
        out: str: outpath
        metric: str
            'all' for all metrics
            'cv', 'cv_ub' for 'unbiased cv', 'serr_pc' for Standard Error in Percentage
    """
    # TODO: Take into account of five different roi types!

    if metric == 'all':
        for m in 'cv', 'cv_ub', 'serr_pc':
            plot_IBI_ITPT_contrast_all_sessions(metric_mats, out, metric=m, bins=bins, same_rank=same_rank,
                                                eps=eps, eigen=True)
        return
    out = os.path.join(out, metric)
    if not os.path.exists(out):
        os.makedirs(out)
    df = metric_mats['window']
    ITdf = df[df['group'] == 'IT']
    PTdf = df[df['group'] == 'PT']
    def generate_dist_series(df, colors, ax):
        animals = df.animal.unique()
        palette = sns.color_palette(colors, n_colors=len(animals))
        for i, a in enumerate(animals):
            sns.distplot(df[df['animal'] == a][metric].dropna(), bins=bins, hist=False, color=palette[i],
                         label=a, ax=ax)

    fig, axes = plt.subplots(nrows=2, ncols=5, sharey=True, figsize=(20, 10))
    for i, t in enumerate(('D', 'IG', 'IR', 'E1', 'E2')):
        ITf, PTf = ITdf[ITdf['roi_type'] == t], PTdf[PTdf['roi_type'] == t]
        if bins is not None:
            axes[0][i].hist([ITf[metric], PTf[metric]], bins=bins, density=True,
                         label=['IT', 'PT'])
        else:
            axes[0][i].hist([ITf[metric], PTf[metric]],
                            bins=best_nbins(ITf[metric]), density=True, label=['IT', 'PT'])
        axes[0][i].legend()
        axes[0][i].set_xlabel('Coefficient of Variation of Interburst Interval (AU)')
        axes[0][i].set_ylabel('Relative Frequency')
        axes[0][i].set_title('ROI type: {}'.format(t))
        generate_dist_series(ITf, 'Blues', axes[1][i])
        generate_dist_series(PTf, 'Reds', axes[1][i])
        axes[1][i].legend()
        axes[1][i].set_title("IBI Contrast IT&PT All Sessions Histogram ")
        axes[1][i].set_xlabel('Coefficient of Variation of Interburst Interval (AU)')
        axes[1][i].set_ylabel('Relative Frequency')
    fig.suptitle('IBI Contrast IT&PT All Sessions Histogram')
    fname = os.path.join(out, "{}IBI_contrast_all_{}{}_roitype".format('all_dist_' if eigen else '', metric, metric_mats['meta']))
    fig.savefig(fname+'.png')
    if eps:
        fig.savefig(fname+".eps")
    plt.close('all')
    # FIG 1
    # if not os.path.exists(out):
    #     os.makedirs(out)
    # IT_metric = metric_mats['IT']['IBIs_window'].ravel()
    # IT_metric = IT_metric[~np.isnan(IT_metric)]
    # PT_metric = metric_mats['PT']['IBIs_window'].ravel()
    # PT_metric = PT_metric[~np.isnan(PT_metric)]
    #
    # if same_rank:
    #     minSize = min(len(IT_metric), len(PT_metric))
    #     inds = np.arange(minSize)
    #     IT_metric = IT_metric[inds]
    #     PT_metric = PT_metric[inds]
    # if eigen is None:
    #     eigen = ['IT', 'PT']
    # fig, axes = plt.subplots(nrows=1, ncols=2, figsize=(20, 10))
    # labels = [eigen[0], eigen[1]]
    # if bins is not None:
    #     axes[0].hist([IT_metric, PT_metric], bins=bins, density=True, label=labels)
    # else:
    #     axes[0].hist([IT_metric, PT_metric], bins=best_nbins(IT_metric), density=True, label=labels)
    # axes[0].legend()
    # con_opt = "{} & {}".format(eigen[0], eigen[1])
    # axes[0].set_title('IBI Contrast {} All Sessions Histogram'.format(con_opt))
    # axes[0].set_xlabel('AU')
    # sns.distplot(IT_metric, bins=bins, hist=False, color="dodgerblue", label=eigen[0], ax=axes[1])
    # sns.distplot(PT_metric, bins=bins, hist=False, color="deeppink", label=eigen[1], ax=axes[1])
    # axes[1].set_title("IBI Contrast {} All Sessions Histogram ".format(con_opt))
    # axes[1].set_xlabel('AU')
    # fname = os.path.join(out, "{}_all_IBI_{}".format("".join(eigen), metric_mats['meta']))
    # fig.savefig(fname+'.png')
    # if eps:
    #     fig.savefig(fname+".eps")


def plot_IBI_ITPT_evolution_days_slides(metric_mats, out, metric='all', eps=True, dropna=True, scatter_off=False, ci='ci'):
    # TODO: ADD between animal comparison (eigen parameter)
    """ Takes in metric mats and plots the evolution plots across windows/days
    Params:
        metric_mats: dict
            {['window': df_window], ['trial': df_trial]}
        out: str: outpath
        df_window: pd.DataFrame
            cols: [roi_type|window|N|cv|cv_ub|serr_pc]
        df_trial: pd.DataFrame
            cols: [trial|HM_trial|N|roi_type|cv|cv_ub|serr_pc]
    """
    # FIG 2
    if metric == 'all':
        for m in 'cv', 'cv_ub', 'serr_pc':
            plot_IBI_ITPT_evolution_days_slides(metric_mats, out, metric=m, eps=eps, scatter_off=scatter_off)
        return
    out = os.path.join(out, metric)
    if not os.path.exists(out):
        os.makedirs(out)
    df = metric_mats['window']
    data = df.dropna() if dropna else df
    if scatter_off:
        sp1 = sns.lmplot(x='session', y=metric, data=data, hue='group', row='roi_type', scatter=False, x_ci=ci)
    else:
        sp1 = sns.lmplot(x='session', y=metric, data=data, hue='group', row='roi_type', x_ci=ci,
               scatter_kws={'alpha': 0.7, 's': 0.1})
    scatter_opt = '_scatteroff' if scatter_off else ''
    drop_opt = "_dropna" if dropna else ''
    fname1 = os.path.join(out, "IBI_evolution_across_days_{}_{}{}{}{}".format(metric, ci, scatter_opt, drop_opt, metric_mats['meta']))
    sp1.savefig(fname1 + '.png')
    if eps:
        sp1.savefig(fname1 + ".eps")
    plt.close('all')
    if scatter_off:
        sp2=sns.lmplot(x='window', y=metric, data=data, hue='group', col='roi_type', scatter=False, x_ci=ci)
    else:
        sp2=sns.lmplot(x='window', y=metric, data=data, hue='group', col='roi_type', x_ci=ci,
            scatter_kws={'alpha': 0.7, 's': 0.1})

    fname2 = os.path.join(out, "IBI_evolution_across_windows_{}_{}{}{}{}".format(metric, ci, scatter_opt, drop_opt, metric_mats['meta']))
    sp2.savefig(fname2 + '.png')
    if eps:
        sp2.savefig(fname2 + ".eps")
    plt.close('all')

    # # FIG 2
    # if not os.path.exists(out):
    #     os.makedirs(out)
    # def get_sequence_over_days(metric_mats, group):
    #     metric = metric_mats[group]['IBIs_window']
    #     all_mean = np.empty(metric.shape[1])
    #     all_serr = np.empty(metric.shape[1])
    #     for d in range(metric.shape[1]):
    #         data = metric[:, d, :, :][metric_mats[group]['redlabel'][:, d, :]].ravel()
    #         all_mean[d] = np.nanmean(data)
    #         all_serr[d] = np.nanstd(data)/np.sum(~np.isnan(data))
    #     return all_mean, all_serr
    #
    # def get_sequence_over_windows(metric_mats, group):
    #     metric = metric_mats[group]['IBIs_window'][metric_mats[group]['redlabel']]
    #     all_mean = np.nanmean(metric, axis=0)
    #     all_serr = np.nanstd(metric, axis=0)/np.sum(~np.isnan(metric), axis=0)
    #     return all_mean, all_serr
    # if eigen is None:
    #     eigen=["IT", "PT"]
    # data = {'IT': {'day': get_sequence_over_days(metric_mats, 'IT'),
    #                'window': get_sequence_over_windows(metric_mats, 'IT')},
    #         'PT': {'day': get_sequence_over_days(metric_mats, 'PT'),
    #                'window': get_sequence_over_windows(metric_mats, 'PT')}}
    # fig, axes = plt.subplots(nrows=1, ncols=2, figsize=(20, 10))
    # for i, t in enumerate(data['IT']):
    #     IT_mean, IT_serr = data['IT'][t]
    #     PT_mean, PT_serr = data['PT'][t]
    #     print(np.mean(IT_serr), np.mean(PT_serr))
    #     axes[i].errorbar(np.arange(1, len(IT_mean) + 1), IT_mean, yerr=IT_serr)
    #     axes[i].errorbar(np.arange(1, len(PT_mean) + 1), PT_mean, yerr=PT_serr)
    #     axes[i].legend(eigen)
    #     axes[i].set_title('{} IBI over {}s'.format(" ".join(eigen), t))
    #     axes[i].set_xlabel(t)
    #     axes[i].set_ylabel('AU')
    # fname = os.path.join(out, "{}_IBI_evolution_{}".format("".join(eigen), metric_mats['meta']))
    # fig.savefig(fname + '.png')
    # if eps:
    #     fig.savefig(fname + ".eps")
    # def get_sequence_over_days(metric_mats, group):
    #     metric = metric_mats[group]['IBIs_window']
    #     all_mean = np.empty(metric.shape[1])
    #     all_serr = np.empty(metric.shape[1])
    #     for d in range(metric.shape[1]):
    #         data = metric[:, d, :, :][metric_mats[group]['redlabel'][:, d, :]].ravel()
    #         all_mean[d] = np.nanmean(data)
    #         all_serr[d] = np.nanstd(data)/np.sum(~np.isnan(data))
    #     return all_mean, all_serr
    #
    # def get_sequence_over_windows(metric_mats, group):
    #     metric = metric_mats[group]['IBIs_window'][metric_mats[group]['redlabel']]
    #     all_mean = np.nanmean(metric, axis=0)
    #     all_serr = np.nanstd(metric, axis=0)/np.sum(~np.isnan(metric), axis=0)
    #     return all_mean, all_serr
    # if eigen is None:
    #     eigen=["IT", "PT"]
    # data = {'IT': {'day': get_sequence_over_days(metric_mats, 'IT'),
    #                'window': get_sequence_over_windows(metric_mats, 'IT')},
    #         'PT': {'day': get_sequence_over_days(metric_mats, 'PT'),
    #                'window': get_sequence_over_windows(metric_mats, 'PT')}}
    # fig, axes = plt.subplots(nrows=1, ncols=2, figsize=(20, 10))
    # for i, t in enumerate(data['IT']):
    #     IT_mean, IT_serr = data['IT'][t]
    #     PT_mean, PT_serr = data['PT'][t]
    #     print(np.mean(IT_serr), np.mean(PT_serr))
    #     axes[i].errorbar(np.arange(1, len(IT_mean) + 1), IT_mean, yerr=IT_serr)
    #     axes[i].errorbar(np.arange(1, len(PT_mean) + 1), PT_mean, yerr=PT_serr)
    #     axes[i].legend(eigen)
    #     axes[i].set_title('{} IBI over {}s'.format(" ".join(eigen), t))
    #     axes[i].set_xlabel(t)
    #     axes[i].set_ylabel('AU')
    # fname = os.path.join(out, "{}_IBI_evolution_{}".format("".join(eigen), metric_mats['meta']))
    # fig.savefig(fname + '.png')
    # if eps:
    #     fig.savefig(fname + ".eps")


def plot_IBI_ITPT_compare_HM(metric_mats, out, metric='all', eps=True, HM=True, dropna=True, scatter_off=False, ci='ci'):
    """ Takes in metric mats and outputs plots of ITPT IBI cv contrast using [metric] across Hits/Miss Trials
    Params:
        metric_mats: dict
            {['window': df_window], ['trial': df_trial]}
        out: str: outpath
        df_window: pd.DataFrame
            cols: [roi_type|window|N|cv|cv_ub|serr_pc]
        df_trial: pd.DataFrame
            cols: [trial|HM_trial|N|roi_type|cv|cv_ub|serr_pc]
    """
    # FIG 3
    if metric == 'all':
        for m in 'cv', 'cv_ub', 'serr_pc':
            plot_IBI_ITPT_compare_HM(metric_mats, out, metric=m, eps=eps, scatter_off=scatter_off)
        return
    out = os.path.join(out, metric)
    if not os.path.exists(out):
        os.makedirs(out)
    df = metric_mats['trial']
    data = df.dropna() if dropna else df
    h = 'HIT/MISS' if HM else 'group'
    c = 'group' if HM else 'HIT/MISS'
    if scatter_off:
        sp1 = sns.lmplot(x='session', y=metric, data=data, hue=h, row='roi_type', col=c, scatter=False, x_ci=ci)
    else:
        sp1 = sns.lmplot(x='session', y=metric, data=data, hue=h, row='roi_type', col=c, x_ci=ci,
               scatter_kws={'alpha': 0.7, 's': 0.1})

    scatter_opt = '_scatteroff' if scatter_off else ''
    drop_opt = '_dropna' if dropna else ''
    fname1 = os.path.join(out, "IBI_HM_compare_across_days_{}_{}{}{}{}".format(metric, ci, scatter_opt, drop_opt, metric_mats['meta']))
    sp1.savefig(fname1 + '.png')
    if eps:
        sp1.savefig(fname1 + ".eps")
    plt.close('all')
    if scatter_off:
        sp2 = sns.lmplot(x='HM_trial', y=metric, data=df.dropna(), hue=h, row='roi_type', col=c, scatter=False, x_ci=ci)
    else:
        sp2 = sns.lmplot(x='HM_trial', y=metric, data=df.dropna(), hue=h, row='roi_type', col=c, x_ci=ci,
               scatter_kws={'alpha': 0.7, 's': 0.1})

    fname2 = os.path.join(out, "IBI_HM_compare_across_trials_{}_{}{}{}{}".format(metric, ci, scatter_opt, drop_opt, metric_mats['meta']))
    sp2.savefig(fname2 + '.png')
    if eps:
        sp2.savefig(fname2 + ".eps")
    plt.close('all')
    # Compares IBI CV distribution in hit and miss trials for IT & PT respectively
    # FIG 3
    # if not os.path.exists(out):
    #     os.makedirs(out)
    # if eigen is None:
    #     eigen = ["IT", "PT"]
    # def get_sequence_over_days(metric_mats, group):
    #     if isinstance(metric_mats, tuple):
    #         metric_mats=metric_mats[0]
    #     metric = metric_mats[group]['IBIs_trial']
    #     res = {}
    #     for t in 'hit', 'miss':
    #         t_mask = metric_mats[group]['array_'+t]
    #         all_mean = np.empty(metric.shape[1])
    #         all_serr = np.empty(metric.shape[1])
    #         for d in range(metric.shape[1]):
    #             mask = np.logical_and(metric_mats[group]['redlabel'][:, d, :, :], t_mask[:, d, :, :])
    #             data = metric[:, d, :, :][mask].ravel()
    #             all_mean[d] = np.nanmean(data)
    #             all_serr[d] = np.nanstd(data)/np.sum(~np.isnan(data))
    #         res[t] = all_mean, all_serr
    #     return res
    #
    # def get_sequence_over_windows(metric_mats, group):
    #     res = {}
    #     if isinstance(metric_mats,tuple):
    #         print(metric_mats[0].keys())
    #         metric_mats = metric_mats[0]
    #     for t in 'hit', 'miss':
    #         mask = np.logical_and(metric_mats[group]['array_' + t], metric_mats[group]['redlabel'])
    #         metric = metric_mats[group]['IBIs_trial'][mask]
    #         all_mean = np.nanmean(metric, axis=0)
    #         all_serr = np.nanstd(metric, axis=0)/np.sum(~np.isnan(metric), axis=0)
    #         res[t] = all_mean, all_serr
    #     return res
    #
    # data = {'IT': {'day': get_sequence_over_days(metric_mats, 'IT'),
    #                'window': get_sequence_over_windows(metric_mats, 'IT')},
    #         'PT': {'day': get_sequence_over_days(metric_mats, 'PT'),
    #                'window': get_sequence_over_windows(metric_mats, 'PT')}}
    # fig, axes = plt.subplots(nrows=1, ncols=2, sharey=True, figsize=(20, 10)) # Each row is [IT, PT]
    # for i, s in enumerate(data['IT']):
    #     # TODO: FIX WINDOW PLOT
    #     if s == 'window':
    #         continue
    #     if HM:
    #         pIT = []
    #         pPT = []
    #         ts = 'hit', 'miss'
    #         for t in ts:
    #             IT_mean, IT_serr = data['IT'][s][t]
    #             PT_mean, PT_serr = data['PT'][s][t]
    #             pIT.append(axes[0].errorbar(np.arange(1, len(IT_mean) + 1), IT_mean, yerr=IT_serr, label=t))
    #             pPT.append(axes[1].errorbar(np.arange(1, len(PT_mean) + 1), PT_mean, yerr=PT_serr, label=t))
    #         axes[0].set_title('{} IBI HIT/MISS trial over {}s'.format(eigen[0], s))
    #         axes[0].set_xlabel(s)
    #         axes[0].set_ylabel('AU')
    #         axes[1].set_title('{} IBI HIT/MISS trial over {}s'.format(eigen[1], s))
    #         axes[1].set_xlabel(s)
    #         axes[0].legend(pIT, ts)
    #         axes[1].legend(pPT, ts)
    #     else:
    #         for j, t in enumerate(('hit', 'miss')):
    #             IT_mean, IT_serr = data['IT'][s][t]
    #             PT_mean, PT_serr = data['PT'][s][t]
    #             axes[i][j].errorbar(np.arange(1, len(IT_mean) + 1), IT_mean, yerr=IT_serr)
    #             axes[i][j].errorbar(np.arange(1, len(PT_mean) + 1), PT_mean, yerr=PT_serr)
    #             axes[i][j].set_title('{} IBI over {}s'.format(" ".join(eigen), s))
    #             axes[i][j].set_xlabel(s)
    #             axes[i][j].set_ylabel('AU')
    # fname = os.path.join(out, "{}_IBI_HMtrial_{}".format("".join(eigen), metric_mats[0]['meta'])) #TODO: identify why metric mat is a tuple
    # fig.savefig(fname + '.png')
    # if eps:
    #     fig.savefig(fname + ".eps")


def plot_IBI_contrast_CVs_ITPTsubset(folder, ITs, PTs, window=None, perc=30, ptp=True, IBI_dist=False,
                                     eps=True):
    """
    Params:
        folder: str
            root folder
        ITs/PTs: dict {animal: [days]}
        window: None or int
                sliding window for calculating IBIs.
                if None, use 'blen' in hdf5 file instead, but inputs have to be str/h5py.File
        perc: float
            hyperparameter for partitioning algorithm, correlated with tail length of splitted calcium trace
        ptp: boolean
            True if IBI is based on peak to peak measurement, otherwise tail to tail
        IBI_dist: boolean
            generate the IBI_distribution matrix if True

    :return:
    """
    hyperparam = 'theta_perc{}{}_window{}'.format(perc, '_ptp' if ptp else "", window)
    processed = os.path.join(folder, 'CaBMI_analysis/processed')
    IBIs = os.path.join(folder, 'bursting/IBI')
    out = os.path.join(folder, 'bursting/plots')
    ITs = parse_group_dict(processed, ITs, 'IT')
    print(ITs)
    PTs = parse_group_dict(processed, PTs, 'PT')
    def get_matrix(group_dict):
        maxA, maxD, maxN, maxS, maxIBI = \
            len(group_dict), max([len(group_dict[a]) for a in group_dict]), 0, 0, 0
        temp = {}
        for animal in group_dict:
            for day in group_dict[animal]:
                with h5py.File(encode_to_filename(processed, animal, day), 'r') as f:
                    redlabel = np.copy(f['redlabel'])
                with h5py.File(encode_to_filename(IBIs, animal, day, hyperparams=hyperparam), 'r') as f:
                    CVs = f['CVs']
                    ibi_dist = f['IBIs']
                    maxN = max(CVs.shape[0], maxN)
                    maxS = max(CVs.shape[1], maxS)
                    maxIBI = max(ibi_dist.shape[-1], maxIBI)
                    if animal in temp:
                        temp[animal][day] = {k: np.copy(f[k]) for k in f.keys()}
                    else:
                        temp[animal] = {day: {k: np.copy(f[k]) for k in f.keys()}}
                    temp[animal][day]['redlabel'] = redlabel
        animal_maps = {}
        metric_mats = {k: np.full((maxA, maxD, maxN, maxS), np.nan) for k in ('CVs', 'mean', 'stds')}
        IBI_mat = np.full((maxA, maxD, maxN, maxS, maxIBI), np.nan)
        redlabels = np.full((maxA, maxD, maxN), False)
        for i, animal in enumerate(temp):
            animal_maps[i] = animal
            for j, d in enumerate(sorted([k for k in temp[animal].keys() if k != 'redlabel'])):
                # TODO: Add day Map, which could be better perfected using navigation.mat
                redlabels[i, j, :len(temp[animal][d]['redlabel'])] = temp[animal][d]['redlabel']
                del temp[animal][d]['redlabel']
                for k in metric_mats:
                    target = temp[animal][d][k]
                    metric_mats[k][i, j, :target.shape[0], :target.shape[1]] = target
                targetIBI = temp[animal][d]['IBIs']
                IBI_mat[i, j, :targetIBI.shape[0], :targetIBI.shape[1], :targetIBI.shape[2]] \
                    = targetIBI
        return metric_mats, IBI_mat, redlabels
    IT_metric, IT_IBI, IT_redlabels = get_matrix(ITs)
    PT_metric, PT_IBI, PT_redlabels = get_matrix(PTs)
    savepath = os.path.join(out, 'IBI')
    if not os.path.exists(savepath):
        os.makedirs(savepath)
    lims = {}
    for k in IT_metric:
        if k != 'IBIs':
            tmax = max(np.nanmax(IT_metric[k][IT_redlabels]), np.nanmax(PT_metric[k][PT_redlabels]))
            tmin = min(np.nanmin(IT_metric[k][IT_redlabels]), np.nanmin(PT_metric[k][PT_redlabels]))
            lims[k] = (tmin*0.9, tmax*1.1)
    tmax = max(np.nanmax(IT_IBI[IT_redlabels]), np.nanmax(PT_IBI[PT_redlabels]))
    tmin = min(np.nanmin(IT_IBI[IT_redlabels]), np.nanmin(PT_IBI[PT_redlabels]))
    lims['IBIs'] = (tmin*0.9, tmax*1.1)
    for s in range(IT_IBI.shape[-2]):
        fig, axes = plt.subplots(nrows=2, ncols=2, figsize=(20, 10))
        for i, k in enumerate(IT_metric):
            tp = IT_metric[k]
            dataIT = tp[IT_redlabels][:, s].reshape(-1)
            dataIT = dataIT[~np.isnan(dataIT)]
            r, c = i // 2, i % 2
            sns.distplot(dataIT, bins=10, kde=True, norm_hist=True, ax=axes[r][c])
            if s < PT_IBI.shape[-2]:
                dataPT = PT_metric[k][PT_redlabels][:, s].reshape(-1)
                dataPT = dataPT[~np.isnan(dataPT)]
                sns.distplot(dataPT, bins=10, kde=True, norm_hist=True,ax=axes[r][c])
            axes[r, c].legend(['IT', 'PT'])
            axes[r, c].set_xlim(lims[k])
            axes[r, c].set_title(k)
        dataIT = IT_IBI[IT_redlabels][:, s, :].reshape(-1)
        dataIT = dataIT[~np.isnan(dataIT)]
        sns.distplot(dataIT, bins=best_nbins(dataIT), kde=True, norm_hist=True, ax=axes[1][1])
        if s < PT_IBI.shape[-2]:
            dataPT = PT_IBI[PT_redlabels][:, s, :].reshape(-1)
            dataPT = dataPT[~np.isnan(dataPT)]
            sns.distplot(dataPT, bins=best_nbins(dataPT), kde=True, norm_hist=True, ax=axes[1][1])
        axes[1, 1].legend(['IT', 'PT'])
        axes[1, 1].set_title('IBI distribution')
        axes[1, 1].set_xlim(lims['IBIs'])
        imgname = "IT_PT_contrast_session_{}".format(s)
        fig.savefig(os.path.join(savepath, imgname+'.png'))
        if eps:
            fig.savefig(os.path.join(savepath, imgname + '.eps'))


def deconv_fano_contrast_single_pair(hIT, hPT, fano_opt='raw', density=True):
    nneg = True
    W = None
    step = 100
    OPT = 'IT VS PT'
    if fano_opt == 'raw':
        fano_metric = neuron_fano
    elif fano_opt == 'norm_pre':
        fano_metric = lambda *args: neuron_fano_norm(*args, pre=True)
    else:
        fano_metric = lambda *args: neuron_fano_norm(*args, pre=False)

    def get_datas(hfile, data_opt, expr_opt):
        redlabels = np.array(hfile['redlabel'])
        datas = np.array(hfile[data_opt])
        blen = hfile.attrs['blen']
        if nneg:
            datas = datas - np.min(datas, axis=1, keepdims=True)
        if expr_opt.find('IT') != -1:
            datas_it = datas[np.logical_and(redlabels, hfile['nerden'])]
            datas_pt = datas[np.logical_and(~redlabels, hfile['nerden'])]
        elif expr_opt.find('PT') != -1:
            datas_it = datas[np.logical_and(~redlabels, hfile['nerden'])]
            datas_pt = datas[np.logical_and(redlabels, hfile['nerden'])]
        else:
            raise RuntimeError('NOT PT OR IT')
        return {'IT': {'N': datas_it.shape[0], 'data': datas_it,
                       'base': datas_it[:, :blen], 'online': datas_it[:, blen:]},
                'PT': {'N': datas_pt.shape[0], 'data': datas_pt,
                       'base': datas_pt[:, :blen], 'online': datas_pt[:, blen:]}}

    def fano_series(all_data, W, step, out=None, label=None):
        datas, datas_base, datas_online, N = all_data['data'], all_data['base'], \
                                             all_data['online'], all_data['N']
        nfanos = np.empty(N)
        base_fanos = np.empty(N)
        online_fanos = np.empty(N)
        for j in range(N):
            fano = fano_metric(datas[j], W, step)
            fano_base = fano_metric(datas_base[j], W, step)
            fano_online = fano_metric(datas_online[j], W, step)
            print(j, fano)
            nfanos[j] = fano
            base_fanos[j] = fano_base
            online_fanos[j] = fano_online
        if out:
            out['nfanos'][label], out['base_fanos'][label], out['online_fanos'][label] = \
                nfanos, base_fanos, online_fanos
        else:
            return out

    datas_IT_expr = get_datas(hIT, 'neuron_act', 'IT')
    datas_PT_expr = get_datas(hPT, 'neuron_act', 'PT')

    def subroutine(W, step):
        vars = ['IT_expr_IT', 'IT_expr_PT', 'PT_expr_IT', 'PT_expr_PT']
        labels = ['nfanos', 'base_fanos', 'online_fanos']
        plot_datas = {'nfanos': {d: None for d in vars},
                      'base_fanos': {d: None for d in vars},
                      'online_fanos': {d: None for d in vars}}
        fano_series(datas_IT_expr['IT'], W, step, plot_datas, 'IT_expr_IT')
        fano_series(datas_IT_expr['PT'], W, step, plot_datas, 'IT_expr_PT')
        fano_series(datas_PT_expr['IT'], W, step, plot_datas, 'PT_expr_IT')
        fano_series(datas_PT_expr['PT'], W, step, plot_datas, 'PT_expr_PT')

        for v in vars:
            ax[0][0].plot(plot_datas['nfanos'][v])
        ax[0][0].legend(vars)
        ax[0][0].set_xlabel("Neuron")
        ax[0][0].set_title("Fano Factor for all neurons")
        all_stats = {l: {v: {} for v in vars} for l in labels}
        all_stats['meta'] = {'W': W if W else -1, 'T': step}
        for i, label in enumerate(labels):
            curr = i+1
            stat = [None] * 12
            r, c = curr // 2, curr % 2
            for j, v in enumerate(vars):
                fanos = plot_datas[label][v]
                # Choice of bin size: Ref: https://www.fmrib.ox.ac.uk/datasets/techrep/tr00mj2/tr00mj2/node24
                # .html
                miu, sigma, N = np.around(np.nanmean(fanos), 5), np.around(np.nanstd(fanos), 5), len(fanos)
                binsize = 3.49 * sigma * N ** (-1/3) # or 2 IQR N ^(-1/3)
                if density:
                    sns.distplot(fanos, bins=int((max(fanos)- min(fanos)) / binsize + 1), kde=True,
                                 norm_hist=True, ax=ax[r][c])
                else:
                    ax[r][c].hist(fanos, bins=int((max(fanos)- min(fanos)) / binsize + 1), density=True,
                              alpha=0.6)
                stat[j], stat[j+4], stat[j+8] = miu, sigma, N
                all_stats[label][v]['mean'] = stat[j]
                all_stats[label][v]['std'] = stat[j+4]
                all_stats[label][v]['N'] = stat[j + 8]
            ax[r][c].legend(vars)
            ax[r][c].set_title(
                "{}, Mean(ITIT, ITPT, PTIT, PTPT): {}|{}|{}|{}\nStd: {}|{}|{}|{}, N: {}|{}|{}|{}"
                               .format(label, *stat), fontsize=10)
        outpath = "/Users/albertqu/Documents/7.Research/BMI/analysis_data/bursty_log"
        io.savemat(os.path.join(outpath, 'fano_{}_stats_{}.mat'.format(fano_opt, all_stats['meta'])),
                   all_stats)
    #plt.style.use('bmh')
    fig, ax = plt.subplots(nrows=2, ncols=2)
    plt.subplots_adjust(bottom=0.3, wspace=0.3, hspace=0.5)
    fig.suptitle(OPT)
    subroutine(W, step)
    axcolor = 'lightgoldenrodyellow'
    axW = plt.axes([0.1, 0.05, 0.8, 0.03], facecolor=axcolor)
    W_slider = Slider(axW, 'Window', valmin=50, valmax=1000, valinit=W if W else -1, valstep=1)
    axstep = plt.axes([0.1, 0.1, 0.8, 0.03], facecolor=axcolor)
    step_slider = Slider(axstep, 'step', valmin=1, valmax=1000, valinit=step, valstep=1)

    def update(val):
        W, step = int(W_slider.val), int(step_slider.val)
        if W == -1:
            W = None
        for cax in ax.ravel():
            cax.clear()
        subroutine(W, step)
        fig.canvas.draw_idle()

    step_slider.on_changed(update)
    W_slider.on_changed(update)
    plt.show()


def deconv_fano_contrast_avg_days(root, fano_opt='raw', W=None, step=100, eps=True):
    all_files = get_PTIT_over_days(root)
    print(all_files)
    nneg = True
    OPT = 'IT VS PT bursting {}'.format(fano_opt)
    if fano_opt == 'raw':
        fano_metric = neuron_fano
    elif fano_opt == 'norm_pre':
        fano_metric = lambda *args: neuron_fano_norm(*args, pre=True)
    else:
        fano_metric = lambda *args: neuron_fano_norm(*args, pre=False)

    def get_datas(hfile, data_opt, expr_opt):
        redlabels = np.array(hfile['redlabel'])
        datas = np.array(hfile[data_opt])
        blen = hfile.attrs['blen']
        if nneg:
            datas = datas - np.min(datas, axis=1, keepdims=True)
        if expr_opt.find('IT') != -1:
            datas_it = datas[np.logical_and(redlabels, hfile['nerden'])]
            datas_pt = datas[np.logical_and(~redlabels, hfile['nerden'])]
        elif expr_opt.find('PT') != -1:
            datas_it = datas[np.logical_and(~redlabels, hfile['nerden'])]
            datas_pt = datas[np.logical_and(redlabels, hfile['nerden'])]
        else:
            raise RuntimeError('NOT PT OR IT')
        return {'IT': {'N': datas_it.shape[0], 'data': datas_it,
                       'base': datas_it[:, :blen], 'online': datas_it[:, blen:]},
                'PT': {'N': datas_pt.shape[0], 'data': datas_pt,
                       'base': datas_pt[:, :blen], 'online': datas_pt[:, blen:]}}

    def fano_series(all_data, W, step, day=None, out=None, label=None):
        datas, datas_base, datas_online, N = all_data['data'], all_data['base'], \
                                             all_data['online'], all_data['N']
        nfanos = np.empty(N)
        base_fanos = np.empty(N)
        online_fanos = np.empty(N)
        for j in range(N):
            fano = fano_metric(datas[j], W, step)
            fano_base = fano_metric(datas_base[j], W, step)
            fano_online = fano_metric(datas_online[j], W, step)
            print(j, fano)
            nfanos[j] = fano
            base_fanos[j] = fano_base
            online_fanos[j] = fano_online
        if out:
            if day:
                if out['nfanos'][day][label] is None:
                    out['nfanos'][day][label], out['base_fanos'][day][label], \
                    out['online_fanos'][day][label] = nfanos, base_fanos, online_fanos
                else:
                    out['nfanos'][day][label] = np.concatenate((out['nfanos'][day][label], nfanos))
                    out['base_fanos'][day][label] = np.concatenate((out['base_fanos'][day][label], base_fanos))
                    out['online_fanos'][day][label] = np.concatenate((out['online_fanos'][day][label], online_fanos))
            else:
                if out['nfanos'][label] is None:
                    out['nfanos'][label], out['base_fanos'][label], out['online_fanos'][label] = \
                        nfanos, base_fanos, online_fanos
                else:
                    out['nfanos'][label] = np.concatenate((out['nfanos'][label], nfanos))
                    out['base_fanos'][label] = np.concatenate((out['base_fanos'][label], base_fanos))
                    out['online_fanos'][label] = np.concatenate((out['online_fanos'][label], online_fanos))
        else:
            return nfanos, base_fanos, online_fanos

    vars = ['IT_expr_IT', 'IT_expr_PT', 'PT_expr_IT', 'PT_expr_PT']
    labels = ['nfanos', 'base_fanos', 'online_fanos']
    day_range = range(1, max(len(all_files['IT']), len(all_files['PT'])))
    plot_datas = {label: {i: {d: None for d in vars} for i in day_range} for label in labels}

    for group in 'IT', 'PT':
        for day in all_files[group]:
            for expr in all_files[group][day]:
                animal, day = decode_from_filename(expr)
                hfile = h5py.File(os.path.join(root, animal, day, expr), 'r')
                for celltype in 'IT', 'PT':
                    print(group, day, celltype)
                    data_expr = get_datas(hfile, 'neuron_act', group)
                    var = '{}_expr_{}'.format(group, celltype)
                    fano_series(data_expr[celltype], W, step, day, plot_datas, var)

    plt.style.use('bmh')
    fig, ax = plt.subplots(nrows=2, ncols=2)
    plt.subplots_adjust(bottom=0.3, wspace=0.3, hspace=0.5)
    fig.suptitle(OPT)
    all_stats = {l: {d: {v: {} for v in vars} for d in day_range} for l in labels}
    all_stats['meta'] = {'W': W if W else -1, 'T': step}
    outpath = "/home/user/bursting/plots/ITPT_contrast"
    if not os.path.exists(outpath):
        os.makedirs(outpath)
    for day in day_range:
        for v in vars:
            ax[0][0].plot(plot_datas['nfanos'][day][v])
        ax[0][0].legend(vars)
        ax[0][0].set_xlabel("Neuron")
        ax[0][0].set_title("Fano Factor for all neurons")

        for i, label in enumerate(labels):
            curr = i + 1
            stat = ['NA'] * 12
            r, c = curr // 2, curr % 2
            legs = []
            for j, v in enumerate(vars):
                fanos = plot_datas[label][day][v]
                # Choice of bin size: Ref: https://www.fmrib.ox.ac.uk/datasets/techrep/tr00mj2/tr00mj2/node24
                # .html
                if fanos is not None:
                    miu, sigma, N = np.around(np.nanmean(fanos), 5), np.around(np.nanstd(fanos), 5), len(fanos)
                    nbins = best_nbins(fanos)
                    ax[r][c].hist(fanos, bins=int((max(fanos) - min(fanos)) / nbins + 1), density=True, alpha=0.6)
                    stat[j], stat[j + 4], stat[j + 8] = miu, sigma, N
                    all_stats[label][day][v]['mean'] = stat[j]
                    all_stats[label][day][v]['std'] = stat[j + 4]
                    all_stats[label][day][v]['N'] = stat[j + 8]
                legs.append(v)

            ax[r][c].legend(legs)
            ax[r][c].set_title("{}".format(label), fontsize=10)
            fig.savefig(os.path.join(outpath, "d{}_ITPT_contrast_deconvFano_{}_{}_{}.png".format(day, fano_opt, W, step)))
            if eps:
                fig.savefig(os.path.join(outpath,"d{}_ITPT_contrast_deconvFano_{}_{}_{}.eps".format(day, fano_opt, W, step)))
            plt.close('all')
    io.savemat(os.path.join(outpath, 'fano_{}_stats_{}.mat'.format(fano_opt, all_stats['meta'])), all_stats)
    io.savemat(os.path.join(outpath, 'plot_data_fano_{}_{}.mat'.format(fano_opt, all_stats['meta'])), plot_datas)


def burstITPT_contrast_plot(file, fano_opt, W, step, eps=True):
    plot_datas = io.loadmat(file)
    OPT='ITPT_contrast'
    fig, ax = plt.subplots(nrows=2, ncols=2)
    plt.subplots_adjust(bottom=0.3, wspace=0.3, hspace=0.5)
    fig.suptitle(OPT)
    outpath = "/home/user/bursting/plots/ITPT_contrast"
    vars = ['IT_expr_IT', 'IT_expr_PT', 'PT_expr_IT', 'PT_expr_PT']
    labels = ['nfanos', 'base_fanos', 'online_fanos']
    day_range = range(1, max(len(plot_datas['IT']), len(plot_datas['PT'])) + 1)
    for day in day_range:
        for v in vars:
            ax[0][0].plot(plot_datas['nfanos'][day][v])
        ax[0][0].legend(vars)
        ax[0][0].set_xlabel("Neuron")
        ax[0][0].set_title("Fano Factor for all neurons")

        for i, label in enumerate(labels):
            curr = i + 1
            r, c = curr // 2, curr % 2
            legs = []
            for j, v in enumerate(vars):
                fanos = plot_datas[label][day][v]
                # Choice of bin size: Ref: https://www.fmrib.ox.ac.uk/datasets/techrep/tr00mj2/tr00mj2/node24
                # .html
                if fanos:
                    miu, sigma, N = np.around(np.nanmean(fanos), 5), np.around(np.nanstd(fanos), 5), len(
                        fanos)
                    nbins = min(best_nbins(fanos), 200)
                    sns.distplot(fanos, bins=nbins, kde=True, ax=ax[r][c])
                legs.append(v)
            ax[r][c].legend(legs)
            ax[r][c].set_title("{}".format(label), fontsize=10)
            fig.savefig(
                os.path.join(outpath, "d{}_ITPT_contrast_deconvFano_{}_{}_{}.png".format(day, fano_opt, W, step)))
            if eps:
                fig.savefig(os.path.join(outpath,
                                         "d{}_ITPT_contrast_deconvFano_{}_{}_{}.eps".format(day, fano_opt,
                                                                                            W, step)))


def deconv_fano_run():
    root = "/home/user/CaBMI_analysis/processed"
    W, T = None, 100
    for opt in 'norm_pre', 'raw', 'norm_post':
        deconv_fano_contrast_avg_days(root, fano_opt=opt, W=W, step=T, eps=True)



# def generate_IBI_plots(folder, out, method=0, metric='cv', eps=True):
#     generate_IBI_plots_base({'IT': {'IT4': '*'}, 'PT': {'PT6': '*'}}, folder, out, 11, metric, eps)
#     generate_IBI_plots_base({'IT': {'IT4': '*'}, 'PT': {'PT6': '*'}}, folder, out, 12, metric, eps)
#
#
# def generate_IBI_plots2(folder, out, method=0, metric='cv', eps=True):
#     for m in [11, 12]:
#         generate_IBI_plots_base({'IT': {'IT{}'.format(i): "*" for i in range(2, 6)},
#             'PT': {'PT6': '*', 'PT7': '*', 'PT9': '*', 'PT12': '*'}}, folder, out, m, metric, eps)
#
# def generate_IBI_plots3(folder, out, method=0, metric='cv', eps=True):
#     generate_IBI_plots_base({'IT': {'IT2': "*"}, 'PT': {'IT3': '*'}}, folder, out, method, metric, eps, eigen=['IT2', 'IT3'])
#     generate_IBI_plots_base({'IT': {'PT6': "*"}, 'PT': {'PT7': '*'}}, folder, out, method, metric, eps, eigen=['PT6', 'PT7'])
#
# def generate_IBI_plots_base(animals, folder, out, method=0, metric='cv', eps=True, eigen=None):
#     ibi_mat = calcium_IBI_all_sessions(folder, animals, method=method)
#     if method == 0:
#         for m in ibi_mat:
#             hp = decode_method_ibi(m)[1]
#             out1 = os.path.join(out, hp, metric)
#             metric_mat_trial = IBI_to_metric_trial(ibi_mat[m], metric=metric, mask=False),
#             metric_mat_window = IBI_to_metric_window(ibi_mat[m], metric=metric, mask=False)
#             plot_IBI_ITPT_contrast_all_sessions(metric_mat_window, out1, eps=eps, eigen=eigen)
#             plot_IBI_ITPT_evolution_days_windows(metric_mat_window, out1, eps=eps,eigen=eigen)
#             plot_IBI_ITPT_compare_HM(metric_mat_trial, out1, eps=eps, eigen=eigen)
#     else:
#         hp = decode_method_ibi(method)[1]
#         out1 = os.path.join(out, hp, metric)
#         metric_mat_trial = IBI_to_metric_trial(ibi_mat, metric=metric, mask=False),
#         metric_mat_window = IBI_to_metric_window(ibi_mat, metric=metric, mask=False)
#         plot_IBI_ITPT_contrast_all_sessions(metric_mat_window, out1, eps=eps, eigen=eigen)
#         plot_IBI_ITPT_evolution_days_windows(metric_mat_window, out1, eps=eps, eigen=eigen)
#         plot_IBI_ITPT_compare_HM(metric_mat_trial, out1, eps=eps, eigen=eigen)


# TODO: write code that only processes specific animal sessions
def generate_IBI_plots_base(root, method=0, eps=True, eigen=True, metric='all', scatter_off=False):
    if method == 0:
        for m in (1, 2, 11, 12):
            generate_IBI_plots_base(root, method=m, eps=eps, eigen=eigen, metric=metric, scatter_off=scatter_off)
        return
    processed = os.path.join(root, 'CaBMI_analysis/processed')
    IBIs = os.path.join(root, 'bursting/IBI')
    out = os.path.join(root, 'bursting/plots/IBI_contrast/IT6_PT12')
    hp = decode_method_ibi(method)[1]
    out1 = os.path.join(out, hp)
    mats = IBI_to_metric_save(IBIs, processed, animals = ['IT6', 'PT12'], window=None, method=method, test=True)
    print("Done with Metrics")
    print("Plotting all contrasts!")
    plot_IBI_ITPT_contrast_all_sessions(mats, out1, eps=eps, eigen=eigen)
    for dna in (True, False):
        for soff in (True, False):
            print("Plotting evolution")
            plot_IBI_ITPT_evolution_days_slides(mats, out1, metric=metric, eps=eps, dropna=dna, scatter_off=soff)
            print("Plotting HM compare")
            plot_IBI_ITPT_compare_HM(mats, out1, metric=metric, eps=eps, dropna=dna, scatter_off=soff)


def generate_IBI_plots_4animals(root, method=0, eps=True, eigen=True, metric='all', scatter_off=False):
    if method == 0:
        for m in (1, 2, 11, 12):
            generate_IBI_plots_4animals(root, method=m, eps=eps, eigen=eigen, metric=metric, scatter_off=scatter_off)
        return
    processed = os.path.join(root, 'CaBMI_analysis/processed')
    IBIs = os.path.join(root, 'bursting/IBI')
    animals = ['IT3', 'IT4', 'IT5', 'IT6', 'PT6', 'PT7', 'PT9', 'PT12']
    out = os.path.join(root, 'bursting/plots/IBI_contrast/{}animals'.format(len(animals) // 2))
    hp = decode_method_ibi(method)[1]
    out1 = os.path.join(out, hp)
    mats = IBI_to_metric_save(IBIs, processed, animals=animals, window=None, method=method, test=True)
    print("Done with Metrics")
    print("Plotting all contrasts!")
    plot_IBI_ITPT_contrast_all_sessions(mats, out1, eps=eps, eigen=eigen)
    # for ci in ('ci', 'sd'):
    #     for dna in (True, False):
    #         for soff in (True, False):
    #             print("Plotting evolution")
    #             plot_IBI_ITPT_evolution_days_slides(mats, out1, metric=metric, eps=eps, dropna=dna, scatter_off=soff, ci=ci)
    #             for hm in (True, False):
    #                 print("Plotting HM compare")
    #                 plot_IBI_ITPT_compare_HM(mats, out1, metric=metric, eps=eps, HM=hm, dropna=dna, scatter_off=soff, ci=ci)


def check_burst(root, method):
    slist = [] 
    hp = decode_method_ibi(method)[1]
    print(hp)
    for animal in os.listdir(root): 
        print(animal)
        animal_path = os.path.join(root, animal) 
        for day in os.listdir(animal_path): 
            daypath = os.path.join(animal_path, day) 
            bflag = False 
            for f in os.listdir(daypath): 
                if f.find(hp) != -1: 
                    bflag = True 
            if not bflag:
                slist.append((animal, day)) 
    return slist



if __name__ == '__main__':
    root = "/home/user/"
    #mat = calcium_IBI_all_sessions(root)
    #plot_IBI_contrast_CVs_ITPTsubset(root, '*', '*')
    out = os.path.join(root, 'bursting/plots/IBI_contrast')
    if not os.path.exists(out):
        os.makedirs(out)
    # for met in ('cv', 'cv_ub', 'serr_pc'):
    #     generate_IBI_plots2(root, out, method=0, metric=met)
    for m in [2]:
        processed = os.path.join(root, 'CaBMI_analysis/processed')
        IBIs = os.path.join(root, 'bursting/IBI')
        calcium_IBI_all_sessions(root, '*', method=m)
        IBI_to_metric_save(IBIs, processed, animals=None, window=None, method=m, test=True)
