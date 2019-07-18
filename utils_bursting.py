import numpy as np
from scipy.signal import find_peaks, find_peaks_cwt
from shuffling_functions import signal_partition
from utils_cabmi import median_absolute_deviation

def fake_neuron(burst, dur, p0=0.3):
    """Burst: bursty ratio signifying after the peak how more likely the neuron would keep firing"""
    p1 = min(burst * p0, 0.99)
    fake = np.zeros(dur)
    i = np.random.geometric(p0)
    while i < dur:
        fake[i] = np.random.geometric(1 - p1)
        b = np.random.random()
        if b < p1:
            i += 2
        else:
            i += np.random.geometric(p0) + 2
    return fake


def neuron_ipi(t_series):
    """Calculates the Inter Peak Interval"""
    return np.diff(t_series)


def neuron_fano(sig, W=None, T=100):
    """Calculates the Fano Factor for signal using W random unreplaced samples of length T"""
    nrow, ncol = len(sig) // T, T
    sigs = np.reshape(sig[:nrow * ncol], (nrow, ncol))
    if W is not None:
        if W < 1:
            W = int(len(sig) * W)
        inds = np.arange(nrow)
        np.random.shuffle(inds)
        sigs = sigs[inds[:W]]
    binned = np.sum(sigs, axis=1)
    m = np.mean(binned)
    if m == 0:
        return np.nan
    v = np.var(binned)
    return v / m


def neuron_ispi(sig):
    disc_deconv, _ = find_peaks(sig)
    peaks = np.where(disc_deconv > 0)[0]
    return neuron_ipi(peaks)


def neuron_calcium_ipri(sig, perc=30, ptp=True):
    """ Returns the IPRI in calcium neural signals, if ptp, calculate it based on peak-to-peak,
    else, calculate it based on tail-to-tail
    """
    peak_regions, IPIs = signal_partition(sig, perc)
    if ptp:
        peaks = [p[0] + np.argmax(sig[p[0]:p[1]]) for p in peak_regions]
        return neuron_ipi(peaks)
    else:
        return [len(ipi) for ipi in IPIs]


def neuron_calcium_ibi_cwt(sig, method, band=(1, 20)):
    """ Returns the IBI in calcium neural signals using cwt with scale band (1, 20) by default
    Params:
        sig: 1D array
        method:
            opt, thres = method // 10, method % 10
            opt: 0: std
                 1: mad
            thres: number of std/mad
        band: (lo, hi)
            widths band for cwt

    NOTE: 1D array only
    """
    lo, hi = band
    opt, th = method // 10, method % 10
    delta = np.nanmedian(sig) + th * median_absolute_deviation(sig) if opt else np.nanmean(sig) + th * np.nanstd(sig)
    peakind = find_peaks_cwt(sig, np.arange(lo, hi))
    if peakind.shape[0] == 0:
        return np.full(0, np.nan)
    peaks = peakind[sig[peakind] > delta]
    return np.diff(peaks)


def neuron_dc_pk_fano(sig, W=None, T=100):
    """Taking in deconvolved signal and calculates the fano"""
    peaks, _ = find_peaks(sig)
    dis_deconv = np.zeros_like(sig)
    dis_deconv[peaks] = sig[peaks]
    return neuron_fano(dis_deconv, W, T)


def neuron_pr_fano(sig, perc=30, W=None, T=100, debug=False):
    """ Returns the IPRI in calcium neural signals, if ptp, calculate it based on peak-to-peak,
    else, calculate it based on tail-to-tail
    """
    peak_regions, IPIs = signal_partition(sig, perc)
    peaks = [p[0] + np.argmax(sig[p[0]:p[1]]) for p in peak_regions]
    sig_prime = np.zeros_like(sig)
    for p in peaks:
        sig_prime[p] = sig[p]
    if debug:
        return neuron_fano(sig_prime, W, T), sig_prime
    else:
        return neuron_fano(sig_prime, W, T)


def neuron_fano_norm(sig, W=None, T=100, lingress=False, pre=True):
    peaks, _ = find_peaks(sig, threshold=1e-08) # use 1E-08 as a threshold to distinguish from 0
    if len(peaks) == 0:
        peaks = [np.argmax(sig)]
        if np.isclose(sig[peaks[0]], 0):
            return 0
    lmaxes = sig[peaks]
    positves = lmaxes[~np.isclose(lmaxes, 0)]
    if lingress:
        A = np.vstack((positves), np.ones_like(positves)).T
    n = np.min(positves)
    if pre:
        return neuron_fano(sig / (n), W, T)
    else:
        return neuron_fano(sig, W, T) / (n)


def IBI_cv_matrix(ibis, metric='cv_ub'):
    ax = len(ibis.shape) - 1
    m = np.nanmean(ibis, axis=ax)
    if metric == 'cv':
        s = np.nanstd(ibis, axis=ax)
    elif metric == 'cv_ub':
        nn = np.sum(~np.isnan(ibis), axis=ax)
        s = (1 + 1 / (4 * nn)) * np.nanstd(ibis, axis=ax)
    elif metric == 'serr_pc':
        nn = np.sum(~np.isnan(ibis), axis=ax)
        s = np.nanstd(ibis, axis=ax) / np.sqrt(nn)
    else:
        raise ValueError("wrong metric")
    m[m == 0] = 1e-16
    return s / m