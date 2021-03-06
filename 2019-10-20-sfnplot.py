#!/usr/bin/env python

import sys
import os
import pickle
import pdb
import re
import h5py
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
import pandas as pd
from mpl_toolkits.mplot3d import Axes3D
import matplotlib.patches as mpatches
from itertools import combinations
from plot_generation_script import *
from utils_loading import *
from scipy.stats import ttest_ind

datadir = "/run/user/1000/gvfs/smb-share:server=typhos.local,share=data_01/NL/layerproject/processed/"
typhosdir = "/run/user/1000/gvfs/smb-share:server=typhos.local,share=data_01/"
pattern = 'full_(IT|PT)(\d+)_(\d+)_.*\.hdf5'


def learning_params(
    animal, day, sec_var='', bin_size=1, end_bin=None):
    '''
    Obtain the learning rate over time, including the fitted linear regression
    model. This function also allows for longer bin sizes.
    Inputs:
        ANIMAL: String; ID of the animal
        DAY: String; date of the experiment in YYMMDD format
        BIN_SIZE: The number of minutes to bin over. Default is one minute
    Outputs:
        HPM: Numpy array; hits per minute
        PERCENTAGE_CORRECT: float; the proportion of hits out of all trials
        REG: The fitted linear regression model
    '''
    
    folder_path = datadir + animal + '/'
    f = h5py.File(
        folder_path + 'full_' + animal + '_' + day + '_' +
        sec_var + '_data.hdf5', 'r'
        ) 
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    fr = f.attrs['fr']
    blen = f.attrs['blen']
    blen_min = blen//600
    hits = np.asarray(f['hits'])
    miss = np.asarray(f['miss'])
    array_t1 = np.asarray(f['array_t1'])
    array_miss = np.asarray(f['array_miss'])
    trial_end = np.asarray(f['trial_end'])
    trial_start = np.asarray(f['trial_start'])
    percentage_correct = hits.shape[0]/trial_end.shape[0]
    bins = np.arange(0, trial_end[-1]/fr, bin_size*60)
    [hpm, xx] = np.histogram(hits/fr, bins)
    hpm = hpm[blen_min//bin_size:]
    xx = -1*(xx[blen_min//bin_size]) + xx[blen_min//bin_size:]
    xx = xx[1:]
    if end_bin is not None:
        end_frame = end_bin//bin_size + 1
        hpm = hpm[:end_frame]
        xx = xx[:end_frame]
    tth = trial_end[array_t1] + 1 - trial_start[array_t1]

    xx_axis = xx/(bin_size*60.0)
    xx_axis = np.expand_dims(xx_axis, axis=1)
    reg = LinearRegression().fit(xx_axis, hpm)
    return xx_axis, hpm, percentage_correct, reg

def check_in_df(df, animal, day):
    if (df['animal'] == animal).any():
        if (df[df['animal'] == animal]['day'] == day).any():
            return True
    return False

# How many of each type of neuron??
def plot_itpt_hpm():
    """
    Aggregates hits per minute across all IT and PT animals. 
    Looks at max hpm in 10 minute windows.
    """

    num_it = 0
    num_pt = 0
    
    for animaldir in os.listdir(datadir):
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            f = h5py.File(animal_path + file_name, 'r')
            redlabel = np.array(f['redlabel'])
            num_neur = np.sum(redlabel) 
            if experiment_type == 'IT':
                num_it += num_neur 
            else:
                num_pt += num_neur 
    print('ITs: ' + str(num_it))
    print('PTs: ' + str(num_pt))
#plot_itpt_hpm()

# # Comparing Hits per minute for IT and PT
def plot_itpt_hpm():
    """
    Aggregates hits per minute across all IT and PT animals. 
    Looks at max hpm in 10 minute windows.
    """

    vals = []
    ids = []
    num_it = 0
    num_pt = 0
    learners, undefined, nonlearners = get_learners(typhos=typhosdir)
    
    for animaldir in os.listdir(datadir):
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            if not check_in_df(learners, animaldir, experiment_date):
                continue
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            _, hpm, _, _ =\
                learning_params(
                    experiment_type + experiment_animal,
                    experiment_date,
                    bin_size=1
                    )
            hpm_3min = np.convolve(hpm, np.ones((3,))/3, mode='valid')
            max_hpm = np.max(hpm_3min)
            hpm_gain = max_hpm - np.mean(hpm[:3])
            hpm_gain = np.mean(hpm)
            if experiment_type == 'IT':
                vals.append(hpm_gain)
                ids.append("IT")
                num_it += 1
            else:
                vals.append(hpm_gain)
                ids.append("PT")
                num_pt += 1 

    # Plot some rectangles
    df = pd.DataFrame({
        'Values': vals, "Neuron Identity": ids
        })
    plt.figure()
    sns.barplot(
        x='Neuron Identity', y='Values', data=df
        )
    plt.title('Gain in HPM from Experiment Beginning')
    plt.savefig('hpm_learners.eps')
    plt.show(block=True)
    with open("hpmgain.p", "wb") as f:
        pickle.dump(df, f)
    
    # T-Test
    its = df.loc[df['Neuron Identity'] == 'IT']['Values']
    pts = df.loc[df['Neuron Identity'] == 'PT']['Values']
    tstat, pval = ttest_ind(its, pts)
    print("T Test Results:")
    print("T-statistic = %d"%tstat)
    print("P-values = " + str(pval))

#plot_itpt_hpm()

# # Within-Session HPM Learning Plots, IT vs PT

# ## Analysing the first ten minutes of all experiments


## Within-session HPM for IT vs PT

def plot_itpt_hpm(bin_size=1, plotting_bin_size=10, num_minutes=200, first_N_experiments=5):
    """
    Aggregates hits per minute across all IT and PT animals. Performs regression
    on the resulting data, and returns the p-value of how different linear
    regression between the two animals are.
    """

    # Getting all hits per minute arrays
    neuron_type = []
    hits = []
    session_time = []
    num_it = 0
    num_pt = 0
    
    for animaldir in os.listdir(datadir):
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        animal_path_files = animal_path_files[:first_N_experiments]
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            try:
                xs, hpm, _, _ = learning_params(
                        experiment_type + experiment_animal,
                        experiment_date,
                        bin_size=1
                        )
            except:
                print("Binning error with " + f.filename)
            hpm = np.convolve(hpm, np.ones((3,))/3, mode="valid")
            idx_offset = xs.size - hpm.size
            if experiment_type == 'IT':
                for idx, hpm_val in enumerate(hpm):
                    neuron_type.append("IT")
                    session_time.append(xs[idx+idx_offset][0])
                    hits.append(hpm_val)
                num_it += 1
            else:
                for idx, hpm_val in enumerate(hpm):
                    neuron_type.append("PT")
                    session_time.append(xs[idx+idx_offset][0])
                    hits.append(hpm_val)
                num_pt += 1

    # Collect data
    df = pd.DataFrame({
        'Neuron Type': neuron_type,
        'HPM': hits,
        'Session Time': session_time
        })
    fig = plt.figure()
    ax = fig.add_subplot(111)
    
    # Simpl plot
    sns.lineplot(
        y="HPM", x="Session Time", hue="Neuron Type", data=df
        )
    ax.set_ylabel('Number of Hits')
    ax.set_xlabel('Minutes into the Experiment')
    plt.title('Hits/min of All Experiments')
    plt.xticks(np.arange(0,65,5), np.arange(0,65,5))
    plt.yticks(np.arange(0.0, 1.8, 0.2), np.arange(0.0, 1.8, 0.2))
    plt.savefig('sfn_itptsession_scaled.eps')
    plt.show(block=True)

plot_itpt_hpm(
    bin_size=5, plotting_bin_size=10, num_minutes=55,
    first_N_experiments=20
    )
sys.exit(0)

# # Across-Session HPM Learning Plots
# ## Analysing max HPM across sessions for IT vs PT mice

# In[114]:


def plot_itpt_hpm():
    """
    Aggregates hits per minute across all IT and PT animals. 
    Looks at max hpm in 10 minute windows.
    """

    # Getting all hits per minute arrays
    IT_train = []
    IT_target = []
    PT_train = []
    PT_target = []
    num_it = 0
    num_pt = 0
    bin_size = 10
    
    for animaldir in os.listdir(datadir):
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        if animaldir.startswith("IT"):
            num_it += 1
        else:
            num_pt += 1
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        session_idx = 0
        
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            try:
                xs, hpm, _, _ =                    learning_params(
                        experiment_type + experiment_animal,
                        experiment_date,
                        bin_size=1
                        )
            except:
                continue            
            # Get running mean over 10-minute windows
            hpm_5min = np.convolve(hpm, np.ones((5,))/5, mode='valid')
            max_hpm = np.max(hpm_5min)
            if experiment_type == 'IT':
                IT_train.append(session_idx)
                IT_target.append(max_hpm)
            else:
                PT_train.append(session_idx)
                PT_target.append(max_hpm)
            session_idx += 1

    # Collect data
    IT_train = np.array(IT_train).squeeze()
    IT_target = np.array(IT_target)
    PT_train = np.array(PT_train).squeeze()
    PT_target = np.array(PT_target)
    fig = plt.figure()
    ax = fig.add_subplot(111)

    # Some options:
    # Order 1, Order 2, Logx True
    sns.lineplot(
        IT_train, IT_target,
        color='lightseagreen', label='IT (%d Animals)'%num_it
        )
    sns.lineplot(
        PT_train, PT_target,
        color='coral', label='PT (%d Animals)'%num_pt
        )
    ax.set_ylabel('Number of Hits')
    ax.set_xlabel('Day')
    plt.title('Max Average HPM')
    plt.legend()
    plt.xticks(np.arange(0,18,2))
    plt.show(block=True)


# In[115]:
#plot_itpt_hpm()

# ## Analysing % Correct across sessions for IT vs PT mice

def plot_itpt_hpm(bin_size=1, plotting_bin_size=10):
    """
    Aggregates hits per minute across all IT and PT animals. Performs regression
    on the resulting data, and returns the p-value of how different linear
    regression between the two animals are.
    """

    # Getting all hits per minute arrays
    IT_train = []
    IT_target = []
    PT_train = []
    PT_target = []
    num_it = 0
    num_pt = 0
    
    for animaldir in os.listdir(datadir):
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        if animaldir.startswith("IT"):
            num_it += 1
        else:
            num_pt += 1
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        session_idx = 0
        
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            _, _, perc, _ =                learning_params(
                    experiment_type + experiment_animal,
                    experiment_date,
                    bin_size=bin_size
                    )
            if experiment_type == 'IT':
                IT_train.append(session_idx)
                IT_target.append(perc)
            else:
                PT_train.append(session_idx)
                PT_target.append(perc)
            session_idx += 1

    # Collect data
    IT_train = np.array(IT_train).squeeze()
    IT_target = np.array(IT_target)
    PT_train = np.array(PT_train).squeeze()
    PT_target = np.array(PT_target)
    fig = plt.figure()
    ax = fig.add_subplot(111)

    # p-val for linear regression slope similarity
    p_val = linreg_pval(IT_train, IT_target, PT_train, PT_target)
    print("Comparing linear regression slopes of IT and PT:")
    print("p-val = " + str(p_val))

    # Some options:
    # Order 1, Order 2, Logx True
    sns.lineplot(
        IT_train, IT_target,
        x_bins=plotting_bin_size,
        color='lightseagreen', label='IT (%d Animals)'%num_it
        )
    sns.lineplot(
        PT_train, PT_target,
        x_bins=plotting_bin_size,
        color='coral', label='PT (%d Animals)'%num_pt
        )
    ax.set_ylabel('Number of Hits')
    ax.set_xlabel('Day')
    plt.title('Percentage Correct Across Sessions')
    plt.legend()
    plt.xticks(np.arange(0,18,2))
    plt.show(block=True)


# In[74]:


#plot_itpt_hpm(
#    bin_size=1, plotting_bin_size=10
#    )


## Within-session HPM for IT Learners vs Non-Learners
def plot_it_hpm(bin_size=1, plotting_bin_size=10, num_minutes=200, first_N_experiments=5):
    """
    Aggregates hits per minute across all IT and PT animals. Performs regression
    on the resulting data, and returns the p-value of how different linear
    regression between the two animals are.
    """

    # Getting all hits per minute arrays
    learner_type = []
    hits = []
    session_time = []
    num_learners = 0
    num_nonlearners = 0
    learners, undefined, nonlearners = get_learners(typhos=typhosdir)
    
    for animaldir in os.listdir(datadir):
        if animaldir.startswith('PT'):
            continue
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        animal_path_files = animal_path_files[:first_N_experiments]
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            xs, hpm, _, _ = learning_params(
                    experiment_type + experiment_animal,
                    experiment_date,
                    bin_size=1
                    )
            hpm = np.convolve(hpm, np.ones((3,))/3)

            # Get the learning type
            if check_in_df(learners, animaldir, experiment_date):
                for idx, x_val in enumerate(xs):
                    if x_val <= num_minutes:
                        learner_type.append("Learner")
                        session_time.append(x_val[0])
                        hits.append(hpm[idx])
                num_learners += 1
            elif check_in_df(nonlearners, animaldir, experiment_date):
                for idx, x_val in enumerate(xs):
                    if x_val <= num_minutes:
                        learner_type.append("Non-Learner")
                        session_time.append(x_val[0])
                        hits.append(hpm[idx])
                num_nonlearners += 1

    # Collect data
    print(num_learners)
    print(num_nonlearners)
    df = pd.DataFrame({
        'Learner Type': learner_type,
        'HPM': hits,
        'Session Time': session_time
        })
    fig = plt.figure()
    ax = fig.add_subplot(111)
    
    # Simpl plot
    sns.lineplot(
        y="HPM", x="Session Time", hue="Learner Type", data=df
        )
    ax.set_ylabel('Number of Hits')
    ax.set_xlabel('Minutes into the Experiment')
    plt.title('Hits/min of All Experiments')
    plt.xticks(np.arange(0,65,5), np.arange(0,65,5))
    plt.yticks(np.arange(0,1.8,0.2), np.arange(0,1.8,0.2))
    plt.savefig('sfn_itlearner.eps')
    plt.show(block=True)

#plot_it_hpm(
#    bin_size=5, plotting_bin_size=10, num_minutes=55,
#    first_N_experiments=20
#    )

def plot_pt_hpm(bin_size=1, plotting_bin_size=10, num_minutes=200, first_N_experiments=5):
    """
    Aggregates hits per minute across all IT and PT animals. Performs regression
    on the resulting data, and returns the p-value of how different linear
    regression between the two animals are.
    """

    # Getting all hits per minute arrays
    learner_type = []
    hits = []
    session_time = []
    num_learners = 0
    num_nonlearners = 0
    learners, undefined, nonlearners = get_learners(typhos=typhosdir)
    
    for animaldir in os.listdir(datadir):
        if animaldir.startswith('IT'):
            continue
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        animal_path_files = animal_path_files[:first_N_experiments]
        for file_name in animal_path_files:
            result = re.search(pattern, file_name)
            if not result:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            xs, hpm, _, _ = learning_params(
                    experiment_type + experiment_animal,
                    experiment_date,
                    bin_size=1
                    )
            hpm = np.convolve(hpm, np.ones((3,))/3)

            # Get the learning type
            if check_in_df(learners, animaldir, experiment_date):
                for idx, x_val in enumerate(xs):
                    if x_val <= num_minutes:
                        learner_type.append("Learner")
                        session_time.append(x_val[0])
                        hits.append(hpm[idx])
                num_learners += 1
            elif check_in_df(nonlearners, animaldir, experiment_date):
                for idx, x_val in enumerate(xs):
                    if x_val <= num_minutes:
                        learner_type.append("Non-Learner")
                        session_time.append(x_val[0])
                        hits.append(hpm[idx])
                num_nonlearners += 1

    # Collect data
    print(num_learners)
    print(num_nonlearners)
    df = pd.DataFrame({
        'Learner Type': learner_type,
        'HPM': hits,
        'Session Time': session_time
        })
    fig = plt.figure()
    ax = fig.add_subplot(111)
    
    # Simpl plot
    sns.lineplot(
        y="HPM", x="Session Time", hue="Learner Type", data=df
        )
    ax.set_ylabel('Number of Hits')
    ax.set_xlabel('Minutes into the Experiment')
    plt.title('Hits/min of All Experiments')
    plt.xticks(np.arange(0,65,5), np.arange(0,65,5))
    plt.yticks(np.arange(0,1.8,0.2), np.arange(0,1.8,0.2))
    plt.savefig('sfn_ptlearner.eps')
    plt.show(block=True)

#plot_pt_hpm(
#    bin_size=5, plotting_bin_size=10, num_minutes=55,
#    first_N_experiments=20
#    )

# ## Analysing % Correct across sessions for IT learners vs PT learners
def plot_itpt_hpm(bin_size=1, plotting_bin_size=10):
    """
    Aggregates hits per minute across all IT and PT animals. Performs regression
    on the resulting data, and returns the p-value of how different linear
    regression between the two animals are.
    """

    # Getting all hits per minute arrays
    animal_type = []
    percent_correct = []
    session_number = []
    num_it = 0
    num_pt = 0
    learners, undefined, nonlearners = get_learners(typhos=typhosdir)
    
    for animaldir in os.listdir(datadir):
        animal_path = datadir + animaldir + '/'
        if not os.path.isdir(animal_path):
            continue
        animal_path_files = os.listdir(animal_path)
        animal_path_files.sort()
        session_idx = 0
        
        for file_name in animal_path_files:
            session_idx += 1
            result = re.search(pattern, file_name)
            if not result or session_idx > 15:
                continue
            experiment_type = result.group(1)
            experiment_animal = result.group(2)
            experiment_date = result.group(3)
#            if not check_in_df(learners, animaldir, experiment_date):
#                continue
            f = h5py.File(animal_path + file_name, 'r')
            com_cm = np.array(f['com_cm'])
            _, _, perc, _ = learning_params(
                    experiment_type + experiment_animal,
                    experiment_date,
                    bin_size=bin_size
                    )
#            xs, hpm, _, _ = learning_params(
#                    experiment_type + experiment_animal,
#                    experiment_date,
#                    bin_size=1
#                    )
            #hpm = np.convolve(hpm, np.ones((3,))/3)
#            perc = np.mean(hpm)
            if experiment_type == 'IT':
                percent_correct.append(perc)
                session_number.append(session_idx)
                animal_type.append('IT')
            else:
                percent_correct.append(perc)
                session_number.append(session_idx)
                animal_type.append('PT')

    # Collect data
    print("Collecting DataFrame")
    print(num_it)
    print(num_pt)
    df = pd.DataFrame({
        'Animal Type': animal_type,
        'Percent Correct': percent_correct,
        'Session Number': session_number
        })
    
    # Simpl plot
    print("Plotting")
    plt.figure()
    sns.lineplot(
        y="Percent Correct", x="Session Number", hue="Animal Type", data=df
        )
    plt.ylabel('Number of Hits')
    plt.xlabel('Day')
    #plt.title('Percentage Correct Across Sessions')
    plt.title('Mean Hits per Minute Across Sessions')
    plt.xticks(np.arange(0,16,5), np.arange(0,16,5))
    plt.yticks(np.arange(0,1.2,0.2), np.arange(0,1.2,0.2))
    plt.savefig('sfn_why.eps')
    plt.show(block=True)

#plot_itpt_hpm(
#    bin_size=1, plotting_bin_size=10
#    )

