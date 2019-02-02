import time
import h5py
import numpy as np
import matplotlib.pyplot as plt
import pdb
import sys
import pandas as pd
import seaborn as sns
import os
import math
import random
import scipy
import copy
from skimage import io
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.axes_grid1 import make_axes_locatable
from mpl_toolkits.axes_grid1 import host_subplot
from matplotlib.ticker import MultipleLocator
from scipy import stats
from scipy.stats.mstats import zscore
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from matplotlib import interactive
import utils_cabmi as ut

def plot_trial_end_all(folder, animal, day,
        trial_type=0, sec_var=''):
    '''
    Plot calcium activity of each neuron from the last 5 seconds before the end
    of a trial to 3 seconds after the trial. The user can choose whether to plot
    all trials, hit trials, or miss trials
    Inputs:
        FOLDER: String; path to folder containing data files
        ANIMAL: String; ID of the animal
        DAY: String; date of the experiment in TTMMDD format
        TRIAL_TYPE: an integer from [0,1,2]. 0 indicates all trials,
            1 indicates hit trials, 2 indicates miss trials.
    '''
    folder_path = folder +  'processed/' + animal + '/' + day + '/'
    folder_anal = folder +  'analysis/learning/' + animal + '/' + day + '/'
    f = h5py.File(
        folder_path + 'full_' + animal + '_' + day + '_' +
        sec_var + '_data.hdf5', 'r'
        )

    t_size = [30,3]
    tbin = 10
    time_lock_data = time_lock_activity(f, t_size, tbin)
    end_frame = time_lock_data.shape[2] - tbin*t_size[1]
    time_lock_data = time_lock_data[:,:,end_frame - tbin*5:]
    num_trials, num_neurons, num_frames = time_lock_data.shape
    end_frame = num_frames - tbin*t_size[1]

    # Sliding plot
    fig, ax = plt.subplots(nrows=1, ncols=1)
    plt.subplots_adjust(bottom=0.25)
    ax.plot(time_lock_data[0,0,:])
    ax.axvline(end_frame, color='r', lw=1.25)
    ax.set_xlabel("Frame Number")
    ax.set_ylabel("Calcium Activity")
    trial_type_names = ["All Trials", "Hit Trials", "Miss Trials"]
    plt.title(
        'Trial End Activity of Neurons:\n'+trial_type_names[trial_type],
        fontsize='large'
        )

    axcolor = 'lightgoldenrodyellow'
    axtrials = plt.axes([0.1, 0.05, 0.8, 0.03], facecolor=axcolor)
    trial_slider = Slider(axtrials, 'Trial', 0, num_trials-1, valinit=0)
    axneurons = plt.axes([0.1, 0.1, 0.8, 0.03], facecolor=axcolor)
    neurons_slider = Slider(axneurons, 'Neuron', 0, num_neurons-1,valinit=0)
    def update(val):
        trial = int(trial_slider.val)
        neuron_idx = int(neurons_slider.val)
        trial_data = time_lock_data[trial,neuron_idx,:]
        for l in ax.get_lines():
            ax.lines.remove(l)
        ax.plot(trial_data)
        ax.axvline(end_frame, color='r', lw=1.25)
        ax.set_ylim((np.min(trial_data), np.max(trial_data)))
        fig.canvas.draw_idle()
    trial_slider.on_changed(update)
    neurons_slider.on_changed(update)
    plt.show()
    
def plot_trial_end_ens(folder, animal, day,
        trial_type=0, sec_var=''):
    '''
    Plot calcium activity of ensemble neurons from the last 5 seconds before the
    end of a trial to 3 seconds after the trial.The slider allows the user to
    view different trials. The user can choose whether to plot all trials,
    hit trials, or miss trials.
    Inputs:
        FOLDER: String; path to folder containing data files
        ANIMAL: String; ID of the animal
        DAY: String; date of the experiment in TTMMDD format
        TRIAL_TYPE: an integer from [0,1,2]. 0 indicates all trials,
            1 indicates hit trials, 2 indicates miss trials.
    '''
    folder_path = folder +  'processed/' + animal + '/' + day + '/'
    folder_anal = folder +  'analysis/learning/' + animal + '/' + day + '/'
    f = h5py.File(
        folder_path + 'full_' + animal + '_' + day + '_' +
        sec_var + '_data.hdf5', 'r'
        )

    t_size = [30,3]
    tbin = 10
    time_lock_data = time_lock_activity(f, t_size, tbin)
    end_frame = time_lock_data.shape[2] - tbin*t_size[1]
    time_lock_data = time_lock_data[:,:,end_frame - tbin*5:]
    num_trials, num_neurons, num_frames = time_lock_data.shape
    end_frame = num_frames - tbin*t_size[1]
    ens_neurons = np.array(f['ens_neur'])

    # Sliding plot
    fig, axs = plt.subplots(nrows=2, ncols=2, sharex=True)
    plt.subplots_adjust(bottom=0.225, top=0.825)
    for icol in range(2):
        for irow in range(2):
            neuron_idx = int(ens_neurons[icol+2*irow])
            axs[irow, icol].plot(time_lock_data[0,neuron_idx,:])
            axs[irow, icol].set_title('Neuron ' + str(neuron_idx))
            axs[irow, icol].axvline(end_frame, color='r', lw=1.25)
        axs[1, icol].set_xlabel("Frame Number")
    for irow in range(2):
        axs[irow, 0].set_ylabel("Calcium Activity")
    trial_type_names = ["All Trials", "Hit Trials", "Miss Trials"]
    fig.suptitle(
        'Trial End Activity of Ensemble Neurons:\n'+trial_type_names[trial_type],
        fontsize='large'
        )

    axcolor = 'lightgoldenrodyellow'
    axtrials = plt.axes([0.1, 0.05, 0.8, 0.03], facecolor=axcolor)
    trial_slider = Slider(axtrials, 'Trial', 0, num_trials-1, valinit=0)
    def update(val):
        trial = int(trial_slider.val)
        for icol in range(2):
            for irow in range(2):
                neuron_idx = int(ens_neurons[icol+2*irow])
                trial_data = time_lock_data[trial,neuron_idx,:]
                for l in axs[irow, icol].get_lines():
                    axs[irow, icol].lines.remove(l)
                axs[irow, icol].plot(trial_data)
                axs[irow, icol].axvline(end_frame, color='r', lw=1.25)
        fig.canvas.draw_idle()
    trial_slider.on_changed(update)
    plt.show()