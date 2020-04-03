import numpy as np
import dxchange
import h5py
import matplotlib.pyplot as plt
import matplotlib
import warnings
from mpi4py import MPI
import datetime
from math import ceil, floor

try:
    import sys
    from scipy.ndimage import gaussian_filter, uniform_filter
    from scipy.ndimage import fourier_shift
except:
    warnings.warn('Some dependencies are screwed up.')
import os
import pickle
import glob
from scipy.special import erf

from adorym.constants import *
import adorym.wrappers as w
from adorym.propagate import *


comm = MPI.COMM_WORLD
n_ranks = comm.Get_size()
rank = comm.Get_rank()


def initialize_object(this_obj_size, dset=None, ds_level=1, object_type='normal', initial_guess=None,
                      output_folder=None, rank=0, n_ranks=1, save_stdout=False, timestr='',
                      shared_file_object=True, not_first_level=False, random_guess_means_sigmas=(8.7e-7, 5.1e-8, 1e-7, 1e-8),
                      unknown_type='delta_beta'):

    if not shared_file_object:
        if rank == 0:
            if not_first_level == False:
                if initial_guess is None:
                    print_flush('Initializing with Gaussian random.', designate_rank=0, this_rank=rank,
                                save_stdout=save_stdout, output_folder=output_folder, timestamp=timestr)
                    obj_delta = np.random.normal(size=this_obj_size, loc=random_guess_means_sigmas[0], scale=random_guess_means_sigmas[2])
                    obj_beta = np.random.normal(size=this_obj_size, loc=random_guess_means_sigmas[1], scale=random_guess_means_sigmas[3])
                else:
                    print_flush('Using supplied initial guess.', designate_rank=0, this_rank=rank, save_stdout=save_stdout,
                                output_folder=output_folder, timestamp=timestr)
                    sys.stdout.flush()
                    obj_delta = np.array(initial_guess[0])
                    obj_beta = np.array(initial_guess[1])
            else:
                print_flush('Initializing with previous pass.', designate_rank=0, this_rank=rank, save_stdout=save_stdout,
                            output_folder=output_folder, timestamp=timestr)
                if unknown_type == 'delta_beta':
                    obj_delta = dxchange.read_tiff(os.path.join(output_folder, 'delta_ds_{}.tiff'.format(ds_level * 2)))
                    obj_beta = dxchange.read_tiff(os.path.join(output_folder, 'beta_ds_{}.tiff'.format(ds_level * 2)))
                elif unknown_type == 'real_imag':
                    obj_delta = dxchange.read_tiff(os.path.join(output_folder, 'obj_mag_ds_{}.tiff'.format(ds_level * 2)))
                    obj_beta = dxchange.read_tiff(os.path.join(output_folder, 'obj_phase_ds_{}.tiff'.format(ds_level * 2)))
                obj_delta = upsample_2x(obj_delta)
                obj_beta = upsample_2x(obj_beta)
                obj_delta += np.random.normal(size=this_obj_size, loc=random_guess_means_sigmas[0], scale=random_guess_means_sigmas[2])
                obj_beta += np.random.normal(size=this_obj_size, loc=random_guess_means_sigmas[1], scale=random_guess_means_sigmas[3])

            # Apply specified constraints.
            if object_type == 'phase_only':
                if unknown_type == 'delta_beta':
                    obj_beta[...] = 0
                elif unknown_type == 'real_imag':
                    obj_delta[...] = 1
            elif object_type == 'absorption_only':
                if unknown_type == 'delta_beta':
                    obj_delta[...] = 0
                elif unknown_type == 'real_imag':
                    obj_beta[...] = 0

            # Apply nonnegativity or convert to real/imag.
            if unknown_type == 'delta_beta':
                obj_delta[obj_delta < 0] = 0
                obj_beta[obj_beta < 0] = 0
            elif unknown_type == 'real_imag':
                obj_delta, obj_beta = mag_phase_to_real_imag(obj_delta, obj_beta)
        else:
            obj_delta = None
            obj_beta = None
        obj_delta = comm.bcast(obj_delta, root=0)
        obj_beta = comm.bcast(obj_beta, root=0)
        return obj_delta, obj_beta
    else:
        if initial_guess is None:
            print_flush('Initializing with Gaussian random.', 0, rank, save_stdout=save_stdout,
                        output_folder=output_folder, timestamp=timestr)
            initialize_hdf5_with_gaussian(dset, rank, n_ranks,
                                          random_guess_means_sigmas[0], random_guess_means_sigmas[2],
                                          random_guess_means_sigmas[1], random_guess_means_sigmas[3],
                                          unknown_type=unknown_type)
        else:
            print_flush('Using supplied initial guess.', 0, rank, save_stdout=save_stdout, output_folder=output_folder,
                        timestamp=timestr)
            if unknown_type == 'real_imag':
                initial_guess = mag_phase_to_real_imag(*initial_guess)
            initialize_hdf5_with_arrays(dset, rank, n_ranks, initial_guess[0], initial_guess[1])
        print_flush('Object HDF5 written.', 0, rank, save_stdout=save_stdout, output_folder=output_folder,
                    timestamp=timestr)
        return


def generate_gaussian_map(size, mag_max, mag_sigma, phase_max, phase_sigma):
    py = np.arange(size[0]) - (size[0] - 1.) / 2
    px = np.arange(size[1]) - (size[1] - 1.) / 2
    pxx, pyy = np.meshgrid(px, py)
    map_mag = mag_max * np.exp(-(pxx ** 2 + pyy ** 2) / (2 * mag_sigma ** 2))
    map_phase = phase_max * np.exp(-(pxx ** 2 + pyy ** 2) / (2 * phase_sigma ** 2))
    return map_mag, map_phase


def initialize_probe(probe_size, probe_type, pupil_function=None, probe_initial=None, rescale_intensity=False,
                     save_stdout=None, output_folder=None, timestr=None, save_path=None, fname=None, **kwargs):
    if probe_type == 'gaussian':
        probe_mag_sigma = kwargs['probe_mag_sigma']
        probe_phase_sigma = kwargs['probe_phase_sigma']
        probe_phase_max = kwargs['probe_phase_max']
        probe_mag, probe_phase = generate_gaussian_map(probe_size, 1, probe_mag_sigma, probe_phase_max, probe_phase_sigma)
        probe_real, probe_imag = mag_phase_to_real_imag(probe_mag, probe_phase)
    elif probe_type == 'aperture_defocus':
        aperture_radius = kwargs['aperture_radius']
        if 'beamstop_radius' in kwargs.keys():
            beamstop_radius = kwargs['beamstop_radius']
        else:
            beamstop_radius = 0
        defocus_cm = kwargs['probe_defocus_cm']
        lmbda_nm = kwargs['lmbda_nm']
        psize_cm = kwargs['psize_cm']
        probe_mag = generate_disk(probe_size, aperture_radius)
        if beamstop_radius > 0:
            beamstop_mask = generate_disk(probe_size, beamstop_radius)
            probe_mag = probe_mag * (1 - beamstop_mask)
        probe_real, probe_imag = mag_phase_to_real_imag(probe_mag, np.zeros_like(probe_mag))
        probe_real, probe_imag = fresnel_propagate(probe_real, probe_imag, defocus_cm * 1e7, lmbda_nm, [psize_cm * 1e7] * 3, override_backend='autograd')
    elif probe_type == 'ifft':
        print_flush('Estimating probe from measured data...', 0, rank, save_stdout=save_stdout,
                    output_folder=output_folder, timestamp=timestr)
        probe_guess_kwargs = {}
        if 'raw_data_type' in kwargs.keys():
            probe_guess_kwargs['raw_data_type'] = kwargs['raw_data_type']
        if 'beamstop' in kwargs.keys() and kwargs['beamstop'] is not None:
            probe_guess_kwargs['beamstop'] = [w.to_numpy(i) for i in kwargs['beamstop']]
        probe_init = create_probe_initial_guess_ptycho(os.path.join(save_path, fname), **probe_guess_kwargs)
        probe_real = probe_init.real
        probe_imag = probe_init.imag
    elif probe_type in ['supplied', 'fixed']:
        probe_mag, probe_phase = probe_initial
        probe_real, probe_imag = mag_phase_to_real_imag(probe_mag, probe_phase)
    elif probe_type == 'plane':
        probe_real = np.ones(probe_size)
        probe_imag = np.zeros(probe_size)
    else:
        raise ValueError('Invalid wavefront type. Choose from \'plane\', \'fixed\', \'supplied\'.')
    if pupil_function is not None:
        probe_real = probe_real * pupil_function
        probe_imag = probe_imag * pupil_function
    if rescale_intensity:
        n_probe_modes = kwargs['n_probe_modes']
        f = h5py.File(fname, 'r')
        dat = f['exchange/data'][...]
        if kwargs['raw_data_type'] == 'magnitude':
            dat = dat ** 2
        if not kwargs['normalize_fft']:
            # The direct return of FFT function has a total power that is n_pixels times of the input.
            # This should be removed.
            intensity_target = np.sum(np.mean(np.abs(dat), axis=(0, 1))) / probe_real.size
        else:
            intensity_target = np.sum(np.mean(np.abs(dat), axis=(0, 1)))
        intensity_current = np.sum(probe_real ** 2 + probe_imag ** 2)
        s = np.sqrt(intensity_target / intensity_current)
        # s = np.sqrt(intensity_target / intensity_current / n_probe_modes)
        probe_real = probe_real * s
        probe_imag = probe_imag * s
        print_flush('Probe magnitude scaling factor is {}.'.format(s), 0, rank, **kwargs['stdout_options'])
    return probe_real, probe_imag


def create_probe_initial_guess(data_fname, dist_nm, energy_ev, psize_nm, raw_data_type='intensity'):

    f = h5py.File(data_fname, 'r')
    dat = f['exchange/data'][...]
    if raw_data_type == 'intensity': dat = np.sqrt(dat)
    # NOTE: this is for toy model
    wavefront = np.mean(np.abs(dat), axis=0)
    lmbda_nm = 1.24 / energy_ev
    h = get_kernel(-dist_nm, lmbda_nm, [psize_nm, psize_nm], wavefront.shape)
    wavefront = np.fft.fftshift(np.fft.fft2(wavefront)) * h
    wavefront = np.fft.ifft2(np.fft.ifftshift(wavefront))
    return wavefront


def create_probe_initial_guess_ptycho(data_fname, noise=False, raw_data_type='intensity', beamstop=None):

    f = h5py.File(data_fname, 'r')
    dat = f['exchange/data'][...]
    if raw_data_type == 'intensity':
        dat = np.sqrt(dat)
    wavefront = np.mean(np.abs(dat), axis=(0, 1))
    if beamstop is not None:
        beamstop_mask = beamstop[0]
        xx, yy =  np.meshgrid(range(beamstop_mask.shape[1]), range(beamstop_mask.shape[0]))
        stop_center_y = np.sum(beamstop_mask * yy) / np.sum(beamstop_mask)
        stop_center_x = np.sum(beamstop_mask * xx) / np.sum(beamstop_mask)
        sigma = np.sqrt(np.count_nonzero(beamstop_mask) / PI)
        gaussian_filler = np.exp(((yy - stop_center_y) ** 2 + (xx - stop_center_x) ** 2) / (-4 * sigma ** 2))
        edge_mask = uniform_filter(beamstop_mask, size=3) - beamstop_mask
        edge_mask[edge_mask > 0] = 1
        edge_mask[edge_mask < 0] = 0
        edge_val = np.sum(edge_mask * wavefront) / np.sum(edge_mask)
        # Scale up the Gaussian filler to match edge values around the beamstop
        gaussian_filler *= (edge_val * np.exp(0.25))
        wavefront = wavefront * (1 - beamstop_mask) + gaussian_filler * beamstop_mask
        # dxchange.write_tiff(wavefront, 'wavefront_initial_guess', dtype='float32', overwrite=True)
    wavefront = abs(np.fft.ifftshift(np.fft.ifft2(wavefront)))
    if noise:
        wavefront_mean = np.mean(wavefront)
        wavefront += np.random.normal(size=wavefront.shape, loc=wavefront_mean, scale=wavefront_mean * 0.2)
        wavefront = np.clip(wavefront, 0, None)
    return wavefront


def preprocess(dat, blur=None, normalize_bg=False):

    dat[np.abs(dat) < 2e-3] = 2e-3
    dat[dat > 1] = 1
    # if normalize_bg:
    #     dat = tomopy.normalize_bg(dat)
    dat = -np.log(dat)
    dat[np.where(np.isnan(dat) == True)] = 0
    if blur is not None:
        dat = gaussian_filter(dat, blur)

    return dat


def realign_image(arr, shift):
    """
    Translate and rotate image via Fourier

    Parameters
    ----------
    arr : ndarray
        Image array.

    shift: tuple
        Mininum and maximum values to rescale data.

    angle: float, optional
        Mininum and maximum values to rescale data.

    Returns
    -------
    ndarray
        Output array.
    """
    # if both shifts are integers, do circular shift; otherwise perform Fourier shift.
    if np.count_nonzero(np.abs(np.array(shift) - np.round(shift)) < 0.01) == 2:
        temp = np.roll(arr, int(shift[0]), axis=0)
        temp = np.roll(temp, int(shift[1]), axis=1)
    else:
        temp = fourier_shift(np.fft.fftn(arr), shift)
        temp = np.fft.ifftn(temp)
    return temp


def realign_image_fourier(a_real, a_imag, shift, axes=(0, 1), device=None):
    """
    Returns real and imaginary parts as a list.
    """
    f_real, f_imag = w.fft2(a_real, a_imag, axes=axes)
    s = f_real.shape
    freq_x, freq_y = np.meshgrid(np.fft.fftfreq(s[axes[1]], 1), np.fft.fftfreq(s[axes[0]], 1))
    freq_x = w.create_variable(freq_x, requires_grad=False, device=device)
    freq_y = w.create_variable(freq_y, requires_grad=False, device=device)
    mult_real, mult_imag = w.exp_complex(0., -2 * PI * (freq_x * shift[1] + freq_y * shift[0]))
    # Reshape for broadcasting
    if len(s) > max(axes) + 1:
        mult_real = w.reshape(mult_real, list(mult_real.shape) + [1] * (len(s) - (max(axes) + 1)))
        mult_real = w.tile(mult_real, [1, 1] + list(s[max(axes) + 1:]))
        mult_imag = w.reshape(mult_imag, list(mult_imag.shape) + [1] * (len(s) - (max(axes) + 1)))
        mult_imag = w.tile(mult_imag, [1, 1] + list(s[max(axes) + 1:]))
    a_real, a_imag = (f_real * mult_real - f_imag * mult_imag, f_real * mult_imag + f_imag * mult_real)
    return w.ifft2(a_real, a_imag, axes=axes)


def create_batches(arr, batch_size):

    arr_len = len(arr)
    i = 0
    batches = []
    while i < arr_len:
        batches.append(arr[i:min(i+batch_size, arr_len)])
        i += batch_size
    return batches


def save_rotation_lookup(array_size, n_theta, dest_folder=None):

    image_center = [np.floor(x / 2) for x in array_size]

    coord0 = np.arange(array_size[0])
    coord1 = np.arange(array_size[1])
    coord2 = np.arange(array_size[2])

    coord2_vec = np.tile(coord2, array_size[1])

    coord1_vec = np.tile(coord1, array_size[2])
    coord1_vec = np.reshape(coord1_vec, [array_size[1], array_size[2]])
    coord1_vec = np.reshape(np.transpose(coord1_vec), [-1])

    coord0_vec = np.tile(coord0, [array_size[1] * array_size[2]])
    coord0_vec = np.reshape(coord0_vec, [array_size[1] * array_size[2], array_size[0]])
    coord0_vec = np.reshape(np.transpose(coord0_vec), [-1])

    # move origin to image center
    coord1_vec = coord1_vec - image_center[1]
    coord2_vec = coord2_vec - image_center[2]

    # create matrix of coordinates
    coord_new = np.stack([coord1_vec, coord2_vec]).astype(np.float32)

    # create rotation matrix
    theta_ls = np.linspace(0, 2 * np.pi, n_theta)
    coord_old_ls = []
    coord_inv_ls = []
    for theta in theta_ls:
        m_rot = np.array([[np.cos(theta),  -np.sin(theta)],
                          [np.sin(theta), np.cos(theta)]])
        coord_old = np.matmul(m_rot, coord_new)
        coord1_old = coord_old[0, :] + image_center[1]
        coord2_old = coord_old[1, :] + image_center[2]
        coord_old = np.stack([coord1_old, coord2_old], axis=1)
        coord_old_ls.append(coord_old)

        m_rot = np.array([[np.cos(-theta),  -np.sin(-theta)],
                          [np.sin(-theta), np.cos(-theta)]])
        coord_inv = np.matmul(m_rot, coord_new)
        coord1_inv = coord_inv[0, :] + image_center[1]
        coord2_inv = coord_inv[1, :] + image_center[2]
        coord_inv = np.stack([coord1_inv, coord2_inv], axis=1)
        coord_inv_ls.append(coord_inv)
    if dest_folder is None:
        dest_folder = 'arrsize_{}_{}_{}_ntheta_{}'.format(array_size[0], array_size[1], array_size[2], n_theta)
    if not os.path.exists(dest_folder):
        os.mkdir(dest_folder)
    # coord_old_ls are the coordinates in original (0-deg) object frame at each angle, corresponding to each
    # voxel in the object at that angle.
    for i, arr in enumerate(coord_old_ls):
        np.save(os.path.join(dest_folder, '{:04}'.format(i)), arr)
    for i, arr in enumerate(coord_inv_ls):
        np.save(os.path.join(dest_folder, '_{:04}'.format(i)), arr)

    # coord_vec's are coordinates list of current object (ordered, e.g. (0, 0, 0), (0, 0, 1), ...)
    coord1_vec = coord1_vec + image_center[1]
    coord1_vec = np.tile(coord1_vec, array_size[0])
    coord2_vec = coord2_vec + image_center[2]
    coord2_vec = np.tile(coord2_vec, array_size[0])
    for i, coord in enumerate([coord0_vec, coord1_vec, coord2_vec]):
        np.save(os.path.join(dest_folder, 'coord{}_vec'.format(i)), coord)

    return coord_old_ls


def read_origin_coords(src_folder, index, reverse=False):

    if not reverse:
        coords = np.load(os.path.join(src_folder, '{:04}.npy'.format(index)), allow_pickle=True)
    else:
        coords = np.load(os.path.join(src_folder, '_{:04}.npy'.format(index)), allow_pickle=True)
    return coords


def read_all_origin_coords(src_folder, n_theta):

    coord_ls = []
    for i in range(n_theta):
        coord_ls.append(read_origin_coords(src_folder, i))
    return coord_ls


def apply_rotation(obj, coord_old, interpolation='bilinear', device=None):

    s = obj.shape
    coord_old = w.create_variable(coord_old, device=device, requires_grad=False)

    if interpolation == 'nearest':
        coord_old_1 = w.round_and_cast(coord_old[:, 0])
        coord_old_2 = w.round_and_cast(coord_old[:, 1])
    else:
        coord_old_1 = coord_old[:, 0]
        coord_old_2 = coord_old[:, 1]

    # Clip coords, so that edge values are used for out-of-array indices
    coord_old_1 = w.clip(coord_old_1, 0, s[1] - 1)
    coord_old_2 = w.clip(coord_old_2, 0, s[2] - 1)

    if interpolation == 'nearest':
        obj_rot = w.reshape(obj[:, coord_old_1, coord_old_2], s)
    else:
        coord_old_floor_1 = w.floor_and_cast(coord_old_1, dtype='int64')
        coord_old_ceil_1 = w.ceil_and_cast(coord_old_1, dtype='int64')
        coord_old_floor_2 = w.floor_and_cast(coord_old_2, dtype='int64')
        coord_old_ceil_2 = w.ceil_and_cast(coord_old_2, dtype='int64')
        # integer_mask_1 = (abs(coord_old_ceil_1 - coord_old_1) < 1e-5).astype(int)
        # integer_mask_2 = (abs(coord_old_ceil_2 - coord_old_2) < 1e-5).astype(int)
        coord_old_floor_1 = w.clip(coord_old_floor_1, 0, s[1] - 1)
        coord_old_floor_2 = w.clip(coord_old_floor_2, 0, s[2] - 1)
        coord_old_ceil_1 = w.clip(coord_old_ceil_1, 0, s[1] - 1)
        coord_old_ceil_2 = w.clip(coord_old_ceil_2, 0, s[2] - 1)
        integer_mask_1 = abs(coord_old_ceil_1 - coord_old_floor_1) < 1e-5
        integer_mask_2 = abs(coord_old_ceil_2 - coord_old_floor_2) < 1e-5

        obj_rot = []
        for i_chan in range(s[-1]):
            vals_ff = obj[:, coord_old_floor_1, coord_old_floor_2, i_chan]
            vals_fc = obj[:, coord_old_floor_1, coord_old_ceil_2, i_chan]
            vals_cf = obj[:, coord_old_ceil_1, coord_old_floor_2, i_chan]
            vals_cc = obj[:, coord_old_ceil_1, coord_old_ceil_2, i_chan]
            vals = vals_ff * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2) + \
                   vals_fc * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_2 - coord_old_floor_2) + \
                   vals_cf * (coord_old_1 - coord_old_floor_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2) + \
                   vals_cc * (coord_old_1 - coord_old_floor_1) * (coord_old_2 - coord_old_floor_2)
            obj_rot.append(w.reshape(vals, s[:-1]))
        obj_rot = w.stack(obj_rot, axis=-1)

    return obj_rot


def apply_rotation_to_hdf5(dset, coord_old, rank, n_ranks, interpolation='bilinear', monochannel=False, dset_2=None):
    """
    If another dataset is used to store the rotated object, pass the dataset object to
    dset_2. If dset_2 is None, rotated object will overwrite the original dataset.
    """
    s = dset.shape
    slice_ls = range(rank, s[0], n_ranks)

    if dset_2 is None: dset_2 = dset

    if interpolation == 'nearest':
        coord_old_1 = np.round(coord_old[:, 0]).astype('int')
        coord_old_2 = np.round(coord_old[:, 1]).astype('int')
    else:
        coord_old_1 = coord_old[:, 0]
        coord_old_2 = coord_old[:, 1]

    # Clip coords, so that edge values are used for out-of-array indices
    coord_old_1 = np.clip(coord_old_1, 0, s[1] - 1)
    coord_old_2 = np.clip(coord_old_2, 0, s[2] - 1)

    if interpolation == 'nearest':
        for i_slice in slice_ls:
            obj = dset[i_slice]
            obj_rot = np.reshape(obj[coord_old_1, coord_old_2], s[1:])
            dset_2[i_slice] = obj_rot
    else:
        coord_old_floor_1 = np.floor(coord_old_1).astype(int)
        coord_old_ceil_1 = np.ceil(coord_old_1).astype(int)
        coord_old_floor_2 = np.floor(coord_old_2).astype(int)
        coord_old_ceil_2 = np.ceil(coord_old_2).astype(int)
        # integer_mask_1 = (abs(coord_old_ceil_1 - coord_old_1) < 1e-5).astype(int)
        # integer_mask_2 = (abs(coord_old_ceil_2 - coord_old_2) < 1e-5).astype(int)
        coord_old_floor_1 = np.clip(coord_old_floor_1, 0, s[1] - 1)
        coord_old_floor_2 = np.clip(coord_old_floor_2, 0, s[2] - 1)
        coord_old_ceil_1 = np.clip(coord_old_ceil_1, 0, s[1] - 1)
        coord_old_ceil_2 = np.clip(coord_old_ceil_2, 0, s[2] - 1)
        integer_mask_1 = abs(coord_old_ceil_1 - coord_old_floor_1) < 1e-5
        integer_mask_2 = abs(coord_old_ceil_2 - coord_old_floor_2) < 1e-5

        for i_slice in slice_ls:
            obj_rot = []
            obj = dset[i_slice]
            if not monochannel:
                for i_chan in range(s[-1]):
                    vals_ff = obj[coord_old_floor_1, coord_old_floor_2, i_chan]
                    vals_fc = obj[coord_old_floor_1, coord_old_ceil_2, i_chan]
                    vals_cf = obj[coord_old_ceil_1, coord_old_floor_2, i_chan]
                    vals_cc = obj[coord_old_ceil_1, coord_old_ceil_2, i_chan]
                    vals = vals_ff * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2) + \
                           vals_fc * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_2 - coord_old_floor_2) + \
                           vals_cf * (coord_old_1 - coord_old_floor_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2) + \
                           vals_cc * (coord_old_1 - coord_old_floor_1) * (coord_old_2 - coord_old_floor_2)
                    obj_rot.append(np.reshape(vals, s[1:-1]))
                obj_rot = np.stack(obj_rot, axis=-1)
            else:
                vals_ff = obj[coord_old_floor_1, coord_old_floor_2]
                vals_fc = obj[coord_old_floor_1, coord_old_ceil_2]
                vals_cf = obj[coord_old_ceil_1, coord_old_floor_2]
                vals_cc = obj[coord_old_ceil_1, coord_old_ceil_2]
                vals = vals_ff * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2) + \
                       vals_fc * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_2 - coord_old_floor_2) + \
                       vals_cf * (coord_old_1 - coord_old_floor_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2) + \
                       vals_cc * (coord_old_1 - coord_old_floor_1) * (coord_old_2 - coord_old_floor_2)
                obj_rot = np.reshape(vals, s[1:3])
            dset_2[i_slice] = obj_rot

    return None


def revert_rotation_to_hdf5(dset, coord_old, rank, n_ranks, interpolation='bilinear', monochannel=False):

    s = dset.shape
    slice_ls = range(rank, s[0], n_ranks)

    if interpolation == 'nearest':
        coord_old_1 = np.round(coord_old[:, 0]).astype('int')
        coord_old_2 = np.round(coord_old[:, 1]).astype('int')
    else:
        coord_old_1 = coord_old[:, 0]
        coord_old_2 = coord_old[:, 1]

    # Clip coords, so that edge values are used for out-of-array indices
    coord_old_1 = np.clip(coord_old_1, 0, s[1] - 1)
    coord_old_2 = np.clip(coord_old_2, 0, s[2] - 1)

    if interpolation == 'nearest':
        for i_slice in slice_ls:
            obj = dset[i_slice]
            obj_rot = np.reshape(obj[coord_old_1, coord_old_2], s[1:])
            dset[i_slice] = obj_rot
    else:
        coord_old_floor_1 = np.floor(coord_old_1).astype(int)
        coord_old_ceil_1 = np.ceil(coord_old_1).astype(int)
        coord_old_floor_2 = np.floor(coord_old_2).astype(int)
        coord_old_ceil_2 = np.ceil(coord_old_2).astype(int)
        # integer_mask_1 = (abs(coord_old_ceil_1 - coord_old_1) < 1e-5).astype(int)
        # integer_mask_2 = (abs(coord_old_ceil_2 - coord_old_2) < 1e-5).astype(int)
        coord_old_floor_1 = np.clip(coord_old_floor_1, 0, s[1] - 1)
        coord_old_floor_2 = np.clip(coord_old_floor_2, 0, s[2] - 1)
        coord_old_ceil_1 = np.clip(coord_old_ceil_1, 0, s[1] - 1)
        coord_old_ceil_2 = np.clip(coord_old_ceil_2, 0, s[2] - 1)
        integer_mask_1 = abs(coord_old_ceil_1 - coord_old_floor_1) < 1e-5
        integer_mask_2 = abs(coord_old_ceil_2 - coord_old_floor_2) < 1e-5

        for i_slice in slice_ls:
            current_arr = dset[i_slice]
            obj = np.zeros_like(current_arr)
            if not monochannel:
                for i_chan in range(s[-1]):
                    obj[coord_old_floor_1, coord_old_floor_2, i_chan] += current_arr[:, :, i_chan].flatten() * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2)
                    obj[coord_old_floor_1, coord_old_ceil_2, i_chan] += current_arr[:, :, i_chan].flatten() * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_2 - coord_old_floor_2)
                    obj[coord_old_ceil_1, coord_old_floor_2, i_chan] += current_arr[:, :, i_chan].flatten() * (coord_old_1 - coord_old_floor_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2)
                    obj[coord_old_ceil_1, coord_old_ceil_2, i_chan] += current_arr[:, :, i_chan].flatten() * (coord_old_1 - coord_old_floor_1) * (coord_old_2 - coord_old_floor_2)
            else:
                current_arr = current_arr.flatten()
                obj[coord_old_floor_1, coord_old_floor_2] += current_arr * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2)
                obj[coord_old_floor_1, coord_old_ceil_2] += current_arr * (coord_old_ceil_1 + integer_mask_1 - coord_old_1) * (coord_old_2 - coord_old_floor_2)
                obj[coord_old_ceil_1, coord_old_floor_2] += current_arr * (coord_old_1 - coord_old_floor_1) * (coord_old_ceil_2 + integer_mask_2 - coord_old_2)
                obj[coord_old_ceil_1, coord_old_ceil_2] += current_arr * (coord_old_1 - coord_old_floor_1) * (coord_old_2 - coord_old_floor_2)
            dset[i_slice] = obj

    return None


def initialize_hdf5_with_gaussian(dset, rank, n_ranks, delta_mu, delta_sigma, beta_mu, beta_sigma, unknown_type='delta_beta'):

    s = dset.shape
    slice_ls = range(rank, s[0], n_ranks)

    np.random.seed(rank)
    for i_slice in slice_ls:
        slice_delta = np.random.normal(size=[s[1], s[2]], loc=delta_mu, scale=delta_sigma)
        slice_beta = np.random.normal(size=[s[1], s[2]], loc=beta_mu, scale=beta_sigma)
        if unknown_type == 'real_imag':
            slice_delta, slice_beta = mag_phase_to_real_imag(slice_delta, slice_beta)
        slice_data = np.stack([slice_delta, slice_beta], axis=-1)
        slice_data[slice_data < 0] = 0
        dset[i_slice] = slice_data
    return None


def initialize_hdf5_with_constant(dset, rank, n_ranks, constant_value=0):

    s = dset.shape
    slice_ls = range(rank, s[0], n_ranks)

    for i_slice in slice_ls:
        dset[i_slice] = np.full(dset[i_slice].shape, constant_value)
    return None


def initialize_hdf5_with_arrays(dset, rank, n_ranks, init_delta, init_beta):

    s = dset.shape
    slice_ls = range(rank, s[0], n_ranks)

    for i_slice in slice_ls:
        slice_data = np.zeros(s[1:])
        if init_beta is not None:
            slice_data[...] = np.stack([init_delta[i_slice], init_beta[i_slice]], axis=-1)
        else:
            slice_data[...] = init_delta[i_slice]
        slice_data[slice_data < 0] = 0
        dset[i_slice] = slice_data
    return None


def get_rotated_subblocks(dset, this_pos_batch, probe_size, whole_object_size, monochannel=False, mode='hdf5', interpolation='bilinear', unknown_type='delta_beta'):
    """
    Get rotated subblocks centering this_pos_batch directly from hdf5.
    :return: [n_pos, y, x, z, 2]
    """
    block_stack = []
    for coords in this_pos_batch:
        if len(coords) == 2:
            # For the case of ptychography
            this_y, this_x = coords
            line_st, line_end = (this_y, this_y + probe_size[0])
            px_st, px_end = (this_x, this_x + probe_size[1])
        else:
            # For the case of full-field
            line_st, line_end, px_st, px_end = coords
        line_st_clip = max([0, line_st])
        line_end_clip = min([whole_object_size[0], line_end])
        px_st_clip = max([0, px_st])
        px_end_clip = min([whole_object_size[1], px_end])
        this_block = dset[line_st_clip:line_end_clip, px_st_clip:px_end_clip, :]
        if sum(abs(np.array([line_st, line_end, px_st, px_end]) -
                   np.array([line_st_clip, line_end_clip, px_st_clip, px_end_clip]))) > 0:
            if not monochannel:
                if unknown_type == 'delta_beta':
                    this_block = np.pad(this_block, [[line_st_clip - line_st, line_end - line_end_clip],
                                                     [px_st_clip - px_st, px_end - px_end_clip],
                                                     [0, 0], [0, 0]], mode='constant')
                elif unknown_type == 'real_imag':
                    this_block = np.stack([np.pad(this_block[:, :, :, 0], [[line_st_clip - line_st, line_end - line_end_clip],
                                                               [px_st_clip - px_st, px_end - px_end_clip],
                                                               [0, 0], [0, 0]], mode='constant', constant_values=1),
                                           np.pad(this_block[:, :, :, 1], [[line_st_clip - line_st, line_end - line_end_clip],
                                                               [px_st_clip - px_st, px_end - px_end_clip],
                                                               [0, 0], [0, 0]], mode='constant', constant_values=0)], axis=-1)
            else:
                this_block = np.pad(this_block, [[line_st_clip - line_st, line_end - line_end_clip],
                                                 [px_st_clip - px_st, px_end - px_end_clip],
                                                 [0, 0]], mode='constant')
        block_stack.append(this_block)
    block_stack = np.stack(block_stack, axis=0)
    return block_stack


def write_subblocks_to_file(dset, this_pos_batch, obj_delta, obj_beta, probe_size, whole_object_size, monochannel=False, interpolation='bilinear'):
    """
    Write data back in the npy. If monochannel, give None to obj_beta.
    """

    if not monochannel:
        obj = np.stack([obj_delta, obj_beta], axis=-1)
    else:
        obj = obj_delta
    for i_batch, coords in enumerate(this_pos_batch):
        if len(coords) == 2:
            # For the case of ptychography
            this_y, this_x = coords
            line_st, line_end = (this_y, this_y + probe_size[0])
            px_st, px_end = (this_x, this_x + probe_size[1])
        else:
            # For the case of full-field
            line_st, line_end, px_st, px_end = coords
        line_st_clip = max([0, line_st])
        line_end_clip = min([whole_object_size[0], line_end])
        px_st_clip = max([0, px_st])
        px_end_clip = min([whole_object_size[1], px_end])

        this_block = obj[i_batch]
        if sum(abs(np.array([line_st, line_end, px_st, px_end]) -
                   np.array([line_st_clip, line_end_clip, px_st_clip, px_end_clip]))) > 0:
            this_block = this_block[line_st_clip - line_st:this_block.shape[0] - (line_end - line_end_clip),
                                    px_st_clip - px_st:this_block.shape[1] - (px_end - px_end_clip), :]
        dset[line_st_clip:line_end_clip, px_st_clip:px_end_clip, :] += this_block
    return


def pad_object(obj_rot, this_obj_size, probe_pos, probe_size, mode='constant', unknown_type='delta_beta', override_backend=None):
    """
    Pad the object with 0 if any of the probes' extents go beyond the object boundary.
    :return: padded object and padding lengths.
    """
    pad_arr = calculate_pad_len(this_obj_size, probe_pos, probe_size, unknown_type)
    if unknown_type == 'delta_beta':
        paap = [[0, 0]] * (len(obj_rot.shape) - 2)
        args = {}
        if mode == 'constant': args['constant_values'] = 0
        obj_rot = w.pad(obj_rot, pad_arr.tolist() + paap, mode=mode, override_backend=override_backend, **args)
    elif unknown_type == 'real_imag':
        paap = [[0, 0]] * (len(obj_rot.shape) - 3)
        args = {}
        if mode == 'constant': args['constant_values'] = 0
        slicer0 = [slice(None)] * (len(obj_rot.shape) - 1) + [0]
        slicer1 = [slice(None)] * (len(obj_rot.shape) - 1) + [1]
        obj_rot = w.stack([w.pad(obj_rot[slicer0], pad_arr.tolist() + paap, mode=mode, override_backend=override_backend, **args),
                           w.pad(obj_rot[slicer1], pad_arr.tolist() + paap, mode=mode, override_backend=override_backend, **args)],
                           axis=-1)
    return obj_rot, pad_arr


def calculate_pad_len(this_obj_size, probe_pos, probe_size, unknown_type='delta_beta'):
    """
    Pad the object with 0 if any of the probes' extents go beyond the object boundary.
    :return: padded object and padding lengths.
    """
    pad_arr = np.array([[0, 0], [0, 0]])
    if unknown_type == 'delta_beta':
        if min(probe_pos[:, 0]) < 0:
            pad_len = -int(min(probe_pos[:, 0]))
            pad_arr[0, 0] = pad_len
        if max(probe_pos[:, 0]) + probe_size[0] > this_obj_size[0]:
            pad_len = int(max(probe_pos[:, 0])) + probe_size[0] - this_obj_size[0]
            pad_arr[0, 1] = pad_len
        if min(probe_pos[:, 1]) < 0:
            pad_len = -int(min(probe_pos[:, 1]))
            pad_arr[1, 0] = pad_len
        if max(probe_pos[:, 1]) + probe_size[1] > this_obj_size[1]:
            pad_len = int(max(probe_pos[:, 1])) + probe_size[0] - this_obj_size[1]
            pad_arr[1, 1] = pad_len
    elif unknown_type == 'real_imag':
        if min(probe_pos[:, 0]) < 0:
            pad_len = -int(min(probe_pos[:, 0]))
            pad_arr[0, 0] = pad_len
        if max(probe_pos[:, 0]) + probe_size[0] > this_obj_size[0]:
            pad_len = int(max(probe_pos[:, 0])) + probe_size[0] - this_obj_size[0]
            pad_arr[0, 1] = pad_len
        if min(probe_pos[:, 1]) < 0:
            pad_len = -int(min(probe_pos[:, 1]))
            pad_arr[1, 0] = pad_len
        if max(probe_pos[:, 1]) + probe_size[1] > this_obj_size[1]:
            pad_len = int(max(probe_pos[:, 1])) + probe_size[0] - this_obj_size[1]
            pad_arr[1, 1] = pad_len
    return pad_arr


def total_variation_3d(arr, axis_offset=0):
    """
    Calculate total variation of a 3D array.
    :param arr: 3D Tensor.
    :return: Scalar.
    """
    res = w.sum(w.abs(w.roll(arr, 1, axes=0 + axis_offset) - arr))
    res = res + w.sum(w.abs(w.roll(arr, 1, axes=1 + axis_offset) - arr))
    res = res + w.sum(w.abs(w.roll(arr, 1, axes=2 + axis_offset) - arr))
    res /= arr.size
    return res


def generate_sphere(shape, radius, anti_aliasing=5):

    shape = np.array(shape)
    radius = int(radius)
    x = np.linspace(-radius, radius, (radius * 2 + 1) * anti_aliasing)
    y = np.linspace(-radius, radius, (radius * 2 + 1) * anti_aliasing)
    z = np.linspace(-radius, radius, (radius * 2 + 1) * anti_aliasing)
    xx, yy, zz = np.meshgrid(x, y, z)
    a = (xx**2 + yy**2 + zz**2 <= radius**2).astype('float')
    res = np.zeros(shape * anti_aliasing)
    center_res = (np.array(res.shape) / 2).astype('int')
    res[center_res[0] - int(a.shape[0] / 2):center_res[0] + int(a.shape[0] / 2),
        center_res[1] - int(a.shape[0] / 2):center_res[1] + int(a.shape[0] / 2),
        center_res[2] - int(a.shape[0] / 2):center_res[2] + int(a.shape[0] / 2)] = a
    res = gaussian_filter(res, 0.5 * anti_aliasing)
    res = res[::anti_aliasing, ::anti_aliasing, ::anti_aliasing]
    return res


def generate_shell(shape, radius, **kwargs):

    sphere1 = generate_sphere(shape, radius + 0.5)
    sphere2 = generate_sphere(shape, radius - 0.5)
    return sphere1 - sphere2


def generate_disk(shape, radius, **kwargs):
    shape = np.array(shape)
    radius = int(radius)
    x = np.arange(shape[1]) - (shape[1] - 1) / 2
    y = np.arange(shape[0]) - (shape[0] - 1) / 2
    xx, yy = np.meshgrid(x, y)
    a = radius - np.sqrt(xx ** 2 + yy ** 2)
    a = np.clip(a, 0, 1)
    return a


def generate_ring(shape, radius, **kwargs):

    disk1 = generate_disk(shape, radius + 0.5)
    disk2 = generate_disk(shape, radius - 0.5)
    return disk1 - disk2


def fourier_shell_correlation(obj, ref, step_size=1, save_path='fsc', save_mask=True):

    if not os.path.exists(save_path):
        os.makedirs(save_path)

    radius_max = int(min(obj.shape) / 2)
    f_obj = np.fft.fftshift(np.fft.fftn(obj))
    f_ref = np.fft.fftshift(np.fft.fftn(ref))
    f_prod = f_obj * np.conjugate(f_ref)
    f_obj_2 = np.real(f_obj * np.conjugate(f_obj))
    f_ref_2 = np.real(f_ref * np.conjugate(f_ref))
    radius_ls = np.arange(1, radius_max, step_size)
    fsc_ls = []
    np.save(os.path.join(save_path, 'radii.npy'), radius_ls)

    for rad in radius_ls:
        print(rad)
        if os.path.exists(os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad)))):
            mask = dxchange.read_tiff(os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad))))
        else:
            mask = generate_shell(obj.shape, rad, anti_aliasing=2)
            if save_mask:
                dxchange.write_tiff(mask, os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad))),
                                    dtype='float32', overwrite=True)
        fsc = abs(np.sum(f_prod * mask))
        fsc /= np.sqrt(np.sum(f_obj_2 * mask) * np.sum(f_ref_2 * mask))
        fsc_ls.append(fsc)
        np.save(os.path.join(save_path, 'fsc.npy'), fsc_ls)

    matplotlib.rcParams['pdf.fonttype'] = 'truetype'
    fontProperties = {'family': 'serif', 'serif': ['Times New Roman'], 'weight': 'normal', 'size': 12}
    plt.rc('font', **fontProperties)
    plt.plot(radius_ls.astype(float) / radius_ls[-1], fsc_ls)
    plt.xlabel('Spatial frequency (1 / Nyquist)')
    plt.ylabel('FSC')
    plt.savefig(os.path.join(save_path, 'fsc.pdf'), format='pdf')


def fourier_ring_correlation(obj, ref, step_size=1, save_path='frc', save_mask=False):

    if not os.path.exists(save_path):
        os.makedirs(save_path)

    radius_max = int(min(obj.shape) / 2)
    f_obj = np.fft.fftshift(np.fft.fft2(obj))
    f_ref = np.fft.fftshift(np.fft.fft2(ref))
    f_prod = f_obj * np.conjugate(f_ref)
    f_obj_2 = np.real(f_obj * np.conjugate(f_obj))
    f_ref_2 = np.real(f_ref * np.conjugate(f_ref))
    radius_ls = np.arange(1, radius_max, step_size)
    fsc_ls = []
    np.save(os.path.join(save_path, 'radii.npy'), radius_ls)

    for rad in radius_ls:
        print(rad)
        if os.path.exists(os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad)))):
            mask = dxchange.read_tiff(os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad))))
        else:
            mask = generate_ring(obj.shape, rad, anti_aliasing=2)
            if save_mask:
                dxchange.write_tiff(mask, os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad))),
                                    dtype='float32', overwrite=True)
        fsc = abs(np.sum(f_prod * mask))
        fsc /= np.sqrt(np.sum(f_obj_2 * mask) * np.sum(f_ref_2 * mask))
        fsc_ls.append(fsc)
        np.save(os.path.join(save_path, 'fsc.npy'), fsc_ls)

    matplotlib.rcParams['pdf.fonttype'] = 'truetype'
    fontProperties = {'family': 'serif', 'serif': ['Times New Roman'], 'weight': 'normal', 'size': 12}
    plt.rc('font', **fontProperties)
    plt.plot(radius_ls.astype(float) / radius_ls[-1], fsc_ls)
    plt.xlabel('Spatial frequency (1 / Nyquist)')
    plt.ylabel('FRC')
    plt.savefig(os.path.join(save_path, 'frc.pdf'), format='pdf')


def upsample_2x(arr):

    if arr.ndim == 4:
        out_arr = np.zeros([arr.shape[0] * 2, arr.shape[1] * 2, arr.shape[2] * 2, arr.shape[3]])
        for i in range(arr.shape[3]):
            out_arr[:, :, :, i] = upsample_2x(arr[:, :, :, i])
    else:
        out_arr = np.zeros([arr.shape[0] * 2, arr.shape[1] * 2, arr.shape[2] * 2])
        out_arr[::2, ::2, ::2] = arr[:, :, :]
        out_arr = gaussian_filter(out_arr, 1)
    return out_arr


def print_flush(a, designate_rank=None, this_rank=None, save_stdout=True, output_folder='', timestamp=''):

    a = '[{}][{}] '.format(str(datetime.datetime.today()), this_rank) + a
    if designate_rank is not None:
        if this_rank == designate_rank:
            print(a)
    else:
        print(a)
    if (designate_rank is None or this_rank == designate_rank) and save_stdout:
        try:
            f = open(os.path.join(output_folder, 'stdout_{}.txt'.format(timestamp)), 'a')
        except:
            os.makedirs(output_folder)
            f = open(os.path.join(output_folder, 'stdout_{}.txt'.format(timestamp)), 'a')
        f.write(a)
        f.write('\n')
        f.close()
    sys.stdout.flush()
    return


def real_imag_to_mag_phase(realpart, imagpart):
    a = realpart + 1j * imagpart
    return np.abs(a), np.angle(a)


def mag_phase_to_real_imag(mag, phase):
    a = mag * np.exp(1j * phase)
    return a.real, a.imag


def multidistance_ctf(prj_ls, dist_cm_ls, psize_cm, energy_kev, kappa=50, sigma_cut=0.01, alpha_1=5e-4, alpha_2=1e-16):

    prj_ls = np.array(prj_ls)
    dist_cm_ls = np.array(dist_cm_ls)
    dist_nm_ls = dist_cm_ls * 1.e7
    lmbda_nm = 1.24 / energy_kev
    psize_nm = psize_cm * 1.e7
    prj_shape = prj_ls.shape[1:]

    u_max = 1. / (2. * psize_nm)
    v_max = 1. / (2. * psize_nm)
    u, v = gen_mesh([v_max, u_max], prj_shape)
    xi_mesh = PI * lmbda_nm * (u ** 2 + v ** 2)
    xi_ls = np.zeros([len(dist_cm_ls), *prj_shape])
    for i in range(len(dist_cm_ls)):
        xi_ls[i] = xi_mesh * dist_nm_ls[i]

    abs_nu = np.sqrt(u ** 2 + v ** 2)
    nu_cut = 0.6 * u_max
    f = 0.5 * (1 - erf((abs_nu - nu_cut) / sigma_cut))
    alpha = alpha_1 * f + alpha_2 * (1 - f)
    phase = np.sum(np.fft.fftshift(np.fft.fft2(prj_ls - 1, axes=(-2, -1)), axes=(-2, -1)) * (np.sin(xi_ls) + 1. / kappa * np.cos(xi_ls)), axis=0)
    phase /= (np.sum(2 * (np.sin(xi_ls) + 1. / kappa * np.cos(xi_ls)) ** 2, axis=0) + alpha)
    phase = np.fft.ifft2(np.fft.ifftshift(phase, axes=(-2, -1)), axes=(-2, -1))

    return np.abs(phase)


def split_tasks(arr, split_size):
    res = []
    ind = 0
    while ind < len(arr):
        res.append(arr[ind:min(ind + split_size, len(arr))])
        ind += split_size
    return res


def get_block_division(original_grid_shape, n_ranks):
    # Must satisfy:
    # 1. n_block_x * n_block_y = n_ranks
    # 2. block_size[0] * n_block_y = original_grid_shape[0]
    # 3. block_size[1] * n_block_x = original_grid_shape[1]
    # 4. At most 1 block per rank
    n_blocks_y = int(np.round(np.sqrt(original_grid_shape[0] / original_grid_shape[1] * n_ranks)))
    n_blocks_x = int(np.round(np.sqrt(original_grid_shape[1] / original_grid_shape[0] * n_ranks)))
    n_blocks = n_blocks_x * n_blocks_y
    block_size = ceil(max([original_grid_shape[0] / n_blocks_y, original_grid_shape[1] / n_blocks_x]))

    while n_blocks > n_ranks:
        if n_blocks_y * block_size - original_grid_shape[0] > n_blocks_x * block_size - original_grid_shape[1]:
            n_blocks_y -= 1
        else:
            n_blocks_x -= 1
        n_blocks = n_blocks_x * n_blocks_y
    # Reiterate for adjusted block arrangement.
    block_size = ceil(max([original_grid_shape[0] / n_blocks_y, original_grid_shape[1] / n_blocks_x]))
    return n_blocks_y, n_blocks_x, n_blocks, block_size


def get_block_range(i_pos, n_blocks_x, block_size):

    line_st = i_pos // n_blocks_x * block_size
    line_end = line_st + block_size
    px_st = i_pos % n_blocks_x * block_size
    px_end = px_st + block_size
    center_y = (line_st + line_end) / 2
    center_x = (px_st + px_end) / 2
    return line_st, line_end, px_st, px_end, center_y, center_x


def convert_to_hdf5_indexing(inds):

    sorted_ind = np.argsort(inds)
    sorted_coords = inds[sorted_ind]
    sorted_coords_unique, unique_pos = np.unique(sorted_coords, return_index=True)
    repeats = np.roll(unique_pos, -1) - unique_pos
    repeats[-1] += len(inds)

    return sorted_ind, sorted_coords_unique, unique_pos, repeats


def reconstruct_hdf5_takeouts(block, repeats, sorted_ind):

    block = np.repeat(block, repeats, axis=1)
    block = block[:, np.argsort(sorted_ind)]
    return block


def get_rotated_subblocks_with_tilt(dset, this_pos_batch, coord_old, probe_size, whole_object_size, monochannel=False,
                          mode='hdf5', interpolation='bilinear'):
    """
    Get rotated subblocks centering this_pos_batch directly from hdf5.
    :return: [n_pos, y, x, z, 2]
    """
    block_stack = []
    for coords in this_pos_batch:
        if len(coords) == 2:
            # For the case of ptychography
            this_y, this_x = coords
            coord0_vec = np.arange(this_y, this_y + probe_size[0])
            coord1_vec = np.arange(this_x, this_x + probe_size[1])
            block_shape = [probe_size[0], probe_size[1], whole_object_size[-1]]
        else:
            # For the case of full-field
            line_st, line_end, px_st, px_end = coords
            coord0_vec = np.arange(line_st, line_end)
            coord1_vec = np.arange(px_st, px_end)
            block_shape = [line_end - line_st, px_end - px_st, whole_object_size[-1]]
        coord2_vec = np.arange(whole_object_size[-1])
        coord1_vec = np.clip(coord1_vec, 0, whole_object_size[1] - 1)
        array_size = (len(coord0_vec), len(coord1_vec), len(coord2_vec))

        coord2_vec = np.tile(coord2_vec, array_size[1])
        coord1_vec = np.repeat(coord1_vec, array_size[2])

        # Flattened sub-block indices in current object frame
        ind_new = coord1_vec * whole_object_size[2] + coord2_vec

        if interpolation == 'nearest':
            # Flattened sub-block indices in original object frame
            ind_old_1 = np.round(coord_old[:, 0][ind_new]).astype(int)
            ind_old_2 = np.round(coord_old[:, 1][ind_new]).astype(int)

        elif interpolation == 'bilinear':

            # Flattened sub-block indices in original object frame
            ind_old_1 = coord_old[:, 0][ind_new]
            ind_old_2 = coord_old[:, 1][ind_new]
            ind_old_float_1 = np.copy(ind_old_1)
            ind_old_float_2 = np.copy(ind_old_2)

            # Concatenate floor and ceil
            seg_len = len(ind_old_1)
            ind_old_1 = np.concatenate([np.floor(ind_old_1).astype(int),
                                        np.floor(ind_old_1).astype(int),
                                        np.ceil(ind_old_1).astype(int),
                                        np.ceil(ind_old_1).astype(int)])
            ind_old_2 = np.concatenate([np.floor(ind_old_2).astype(int),
                                        np.ceil(ind_old_2).astype(int),
                                        np.floor(ind_old_2).astype(int),
                                        np.ceil(ind_old_2).astype(int)])

        # Clip coords so that edge values are used for out-of-array indices
        ind_old_1 = np.clip(ind_old_1, 0, whole_object_size[1] - 1)
        ind_old_2 = np.clip(ind_old_2, 0, whole_object_size[2] - 1)

        ind_old = ind_old_1 * whole_object_size[1] + ind_old_2

        # Take data with flattened 2nd and 3rd dimensions
        # H5py only supports taking elements using monotonically increasing indices without repeating.

        sorted_ind, sorted_coords_unique, unique_pos, repeats = convert_to_hdf5_indexing(ind_old)

        this_block_temp = dset[max([0, coord0_vec[0]]):min([whole_object_size[0], coord0_vec[-1] + 1]),
                          sorted_coords_unique, :]
        this_block = reconstruct_hdf5_takeouts(this_block_temp, repeats, sorted_ind)

        if interpolation == 'bilinear':
            this_block_ff = this_block[:, 0 * seg_len:1 * seg_len]
            this_block_fc = this_block[:, 1 * seg_len:2 * seg_len]
            this_block_cf = this_block[:, 2 * seg_len:3 * seg_len]
            this_block_cc = this_block[:, 3 * seg_len:4 * seg_len]
            this_block = np.zeros_like(this_block_ff)
            integer_mask_1 = (abs(np.ceil(ind_old_float_1) - ind_old_float_1) < 1e-5).astype(float)
            integer_mask_2 = (abs(np.ceil(ind_old_float_2) - ind_old_float_2) < 1e-5).astype(float)
            if not monochannel:
                for i_chan in range(this_block.shape[2]):
                    this_block[:, :, i_chan] = this_block_ff[:, :, i_chan] * (
                                (np.ceil(ind_old_float_1) + integer_mask_1) - ind_old_float_1) * ((np.ceil(
                        ind_old_float_2) + integer_mask_2) - ind_old_float_2) + \
                                               this_block_fc[:, :, i_chan] * ((np.ceil(
                        ind_old_float_1) + integer_mask_1) - ind_old_float_1) * (
                                                           ind_old_float_2 - np.floor(ind_old_float_2)) + \
                                               this_block_cf[:, :, i_chan] * (
                                                           ind_old_float_1 - np.floor(ind_old_float_1)) * ((np.ceil(
                        ind_old_float_2) + integer_mask_2) - ind_old_float_2) + \
                                               this_block_cc[:, :, i_chan] * (
                                                           ind_old_float_1 - np.floor(ind_old_float_1)) * (
                                                           ind_old_float_2 - np.floor(ind_old_float_2))
            else:
                this_block[:, :] = this_block_ff[:, :] * (
                            (np.ceil(ind_old_float_1) + integer_mask_1) - ind_old_float_1) * (
                                               (np.ceil(ind_old_float_2) + integer_mask_2) - ind_old_float_2) + \
                                   this_block_fc[:, :] * (
                                               (np.ceil(ind_old_float_1) + integer_mask_1) - ind_old_float_1) * (
                                               ind_old_float_2 - np.floor(ind_old_float_2)) + \
                                   this_block_cf[:, :] * (ind_old_float_1 - np.floor(ind_old_float_1)) * (
                                               (np.ceil(ind_old_float_2) + integer_mask_2) - ind_old_float_2) + \
                                   this_block_cc[:, :] * (ind_old_float_1 - np.floor(ind_old_float_1)) * (
                                               ind_old_float_2 - np.floor(ind_old_float_2))

        # Reshape and pad
        if not monochannel:
            this_block = np.reshape(this_block, [this_block.shape[0], block_shape[1], whole_object_size[2], 2])
            if coord0_vec[0] < 0:
                this_block = np.pad(this_block, [[-coord0_vec[0], 0], [0, 0], [0, 0], [0, 0]], mode='edge')
            if coord0_vec[-1] + 1 - whole_object_size[0] > 0:
                this_block = np.pad(this_block,
                                    [[0, coord0_vec[-1] + 1 - whole_object_size[0]], [0, 0], [0, 0], [0, 0]],
                                    mode='edge')
        else:
            this_block = np.reshape(this_block, [this_block.shape[0], block_shape[1], whole_object_size[2]])
            if coord0_vec[0] < 0:
                this_block = np.pad(this_block, [[-coord0_vec[0], 0], [0, 0], [0, 0]], mode='edge')
            if coord0_vec[-1] + 1 - whole_object_size[0] > 0:
                this_block = np.pad(this_block,
                                    [[0, coord0_vec[-1] + 1 - whole_object_size[0]], [0, 0], [0, 0]],
                                    mode='edge')
        # dxchange.write_tiff(this_block[:, :, :, 0], '/Users/ming/Research/Programs/du/adorym_dev/adhesin_ptycho_2/test_bilinear/debug/patch', dtype='float32', overwrite=True)
        # dxchange.write_tiff(np.reshape(this_block_ff, [this_block_ff.shape[0], block_shape[1], whole_object_size[2], 2])[:, :, :, 0], '/Users/ming/Research/Programs/du/adorym_dev/adhesin_ptycho_2/test_bilinear/debug/ff', dtype='float32')
        # dxchange.write_tiff(np.reshape(this_block_fc, [this_block_fc.shape[0], block_shape[1], whole_object_size[2], 2])[:, :, :, 0], '/Users/ming/Research/Programs/du/adorym_dev/adhesin_ptycho_2/test_bilinear/debug/fc', dtype='float32')
        # dxchange.write_tiff(np.reshape(this_block_cf, [this_block_cf.shape[0], block_shape[1], whole_object_size[2], 2])[:, :, :, 0], '/Users/ming/Research/Programs/du/adorym_dev/adhesin_ptycho_2/test_bilinear/debug/cf', dtype='float32')
        # dxchange.write_tiff(np.reshape(this_block_cc, [this_block_cc.shape[0], block_shape[1], whole_object_size[2], 2])[:, :, :, 0], '/Users/ming/Research/Programs/du/adorym_dev/adhesin_ptycho_2/test_bilinear/debug/cc', dtype='float32')
        block_stack.append(this_block)

    block_stack = np.stack(block_stack, axis=0)
    return block_stack


def write_subblocks_to_file_with_tilt(dset, this_pos_batch, obj_delta, obj_beta, coord_old, coord_new, probe_size,
                            whole_object_size, monochannel=False, interpolation='bilinear'):
    """
    Write data back in the npy. If monochannel, give None to obj_beta.
    """

    for i_batch, coords in enumerate(this_pos_batch):
        if len(coords) == 2:
            this_y, this_x = coords
            coord0_vec = np.arange(this_y, this_y + probe_size[0])
            coord1_vec = np.arange(this_x, this_x + probe_size[1])
        else:
            line_st, line_end, px_st, px_end = coords
            coord0_vec = np.arange(line_st, line_end)
            coord1_vec = np.arange(px_st, px_end)
        coord2_vec = np.arange(whole_object_size[2])

        # Mask for coordinates in the rotated-object frame that are inside the array
        array_size = (len(coord0_vec), len(coord1_vec), len(coord2_vec))

        coord2_vec = np.tile(coord2_vec, array_size[1])
        coord1_vec = np.repeat(coord1_vec, array_size[2])

        # Flattened sub-block indices in current object frame
        ind_new = coord1_vec * whole_object_size[2] + coord2_vec
        ind_new = ind_new[(ind_new >= 0) * (ind_new <= coord_old.shape[0] - 1)]

        # Relevant indices in original object frame, expanding selection to both floors and ceils
        ind_old_1 = coord_old[:, 0][ind_new].astype(int)
        # ind_old_1 = np.concatenate([ind_old_1 - 1, ind_old_1, ind_old_1 + 1])
        ind_old_2 = coord_old[:, 1][ind_new].astype(int)
        # ind_old_2 = np.concatenate([ind_old_2 - 1, ind_old_2, ind_old_2 + 1])

        # Mask for coordinates in the old-object frame that are inside the array
        coord_old_clip_mask = (ind_old_1 >= 0) * (ind_old_1 <= whole_object_size[1] - 1) * \
                              (ind_old_2 >= 0) * (ind_old_2 <= whole_object_size[2] - 1)
        ind_old_1 = ind_old_1[coord_old_clip_mask]
        ind_old_2 = ind_old_2[coord_old_clip_mask]

        ind_old = ind_old_1 * whole_object_size[1] + ind_old_2
        discont_pos = np.roll(ind_old, -1) - ind_old - 1
        discont_pos = np.nonzero(discont_pos)[0]
        discont_pos = ind_old[discont_pos]
        # mask
        discont_pos = discont_pos[discont_pos < (whole_object_size[1] - 1) * (whole_object_size[2])]
        discont_pos = discont_pos[discont_pos % whole_object_size[2] != whole_object_size[2] - 1]
        ind_old = np.concatenate([ind_old, discont_pos + 1])

        discont_pos = np.roll(ind_old, 1) - ind_old - 1
        discont_pos = np.nonzero(discont_pos)[0]
        discont_pos = ind_old[discont_pos]
        # mask
        discont_pos = discont_pos[discont_pos > whole_object_size[2]]
        discont_pos = discont_pos[discont_pos % whole_object_size[2] != 0]
        ind_old = np.concatenate([ind_old, discont_pos - 1])

        # These are the voxels in the HDF5 that we need to update.
        _, ind_old, _, _ = convert_to_hdf5_indexing(ind_old)
        ind_old = ind_old[(ind_old >= 0) * (ind_old <= coord_new.shape[0] - 1)]

        # import matplotlib.pyplot as plt
        #
        # x = np.zeros(64 * 64)
        # x[ind_old] = 1
        # x = np.reshape(x, [64, 64])
        #
        # # x = np.zeros([64, 64])
        # # x[ind_old_1, ind_old_2] = 1
        #
        # plt.imshow(x)
        # plt.show()
        # plt.savefig('/Users/ming/Research/Programs/du/adorym_dev/adhesin_ptycho_2/test_bilinear/debug/x_{}.png'.format(i_batch))

        # Get corresponding coordinates in rotated object array.
        ind_new_1 = coord_new[:, 0][ind_old]
        ind_new_2 = coord_new[:, 1][ind_old]

        # Convert x-index to local chunk frame.
        ind_new_1 = ind_new_1 - this_x

        # Calculate y-axis cropping.
        obj_crop_top = max([0, -coord0_vec[0]])
        obj_crop_bot = min([obj_delta.shape[1] - (coord0_vec[-1] + 1 - whole_object_size[0]),
                            obj_delta.shape[1]])

        # Get values from obj_delta and obj_beta.
        if interpolation == 'bilinear':
            ind_new_floor_1 = np.floor(ind_new_1).astype(int)
            ind_new_ceil_1 = np.ceil(ind_new_1).astype(int)
            ind_new_floor_2 = np.floor(ind_new_2).astype(int)
            ind_new_ceil_2 = np.ceil(ind_new_2).astype(int)
            ind_new_floor_1 = np.clip(ind_new_floor_1, 0, obj_delta.shape[2] - 1)
            ind_new_floor_2 = np.clip(ind_new_floor_2, 0, obj_delta.shape[3] - 1)
            ind_new_ceil_1 = np.clip(ind_new_ceil_1, 0, obj_delta.shape[2] - 1)
            ind_new_ceil_2 = np.clip(ind_new_ceil_2, 0, obj_delta.shape[3] - 1)
            # Mask for positions where floors and ceils are equal. In bilinear interpolation the
            # ceils must be added 1 for these positions to prevent getting 0 value.
            integer_mask_1 = abs(ind_new_ceil_1 - ind_new_floor_1) < 1e-5
            integer_mask_2 = abs(ind_new_ceil_2 - ind_new_floor_2) < 1e-5

            vals_delta_ff = obj_delta[i_batch, obj_crop_top:obj_crop_bot, ind_new_floor_1, ind_new_floor_2].transpose()
            vals_delta_fc = obj_delta[i_batch, obj_crop_top:obj_crop_bot, ind_new_floor_1, ind_new_ceil_2].transpose()
            vals_delta_cf = obj_delta[i_batch, obj_crop_top:obj_crop_bot, ind_new_ceil_1, ind_new_floor_2].transpose()
            vals_delta_cc = obj_delta[i_batch, obj_crop_top:obj_crop_bot, ind_new_ceil_1, ind_new_ceil_2].transpose()
            vals_delta = vals_delta_ff * (ind_new_ceil_1 + integer_mask_1 - ind_new_1) * (
                        ind_new_ceil_2 + integer_mask_2 - ind_new_2) + \
                         vals_delta_fc * (ind_new_ceil_1 + integer_mask_1 - ind_new_1) * (ind_new_2 - ind_new_floor_2) + \
                         vals_delta_cf * (ind_new_1 - ind_new_floor_1) * (ind_new_ceil_2 + integer_mask_2 - ind_new_2) + \
                         vals_delta_cc * (ind_new_1 - ind_new_floor_1) * (ind_new_2 - ind_new_floor_2)
            if not monochannel:
                vals_beta_ff = obj_beta[i_batch, obj_crop_top:obj_crop_bot, ind_new_floor_1,
                               ind_new_floor_2].transpose()
                vals_beta_fc = obj_beta[i_batch, obj_crop_top:obj_crop_bot, ind_new_floor_1, ind_new_ceil_2].transpose()
                vals_beta_cf = obj_beta[i_batch, obj_crop_top:obj_crop_bot, ind_new_ceil_1, ind_new_floor_2].transpose()
                vals_beta_cc = obj_beta[i_batch, obj_crop_top:obj_crop_bot, ind_new_ceil_1, ind_new_ceil_2].transpose()
                vals_beta = vals_beta_ff * (ind_new_ceil_1 + integer_mask_1 - ind_new_1) * (
                            ind_new_ceil_2 + integer_mask_2 - ind_new_2) + \
                            vals_beta_fc * (ind_new_ceil_1 + integer_mask_1 - ind_new_1) * (
                                        ind_new_2 - ind_new_floor_2) + \
                            vals_beta_cf * (ind_new_1 - ind_new_floor_1) * (
                                        ind_new_ceil_2 + integer_mask_2 - ind_new_2) + \
                            vals_beta_cc * (ind_new_1 - ind_new_floor_1) * (ind_new_2 - ind_new_floor_2)
        else:
            ind_new_1 = np.round(ind_new_1).astype(int)
            ind_new_2 = np.round(ind_new_2).astype(int)
            vals_delta = obj_delta[i_batch, :, ind_new_1, ind_new_2]
            if not monochannel:
                vals_beta = obj_beta[i_batch, :, ind_new_1, ind_new_2]

        # Write in values.
        if not monochannel:
            dset[max([0, coord0_vec[0]]):min([whole_object_size[0], coord0_vec[-1] + 1]), ind_old, :] += \
                np.stack([vals_delta, vals_beta], axis=-1)
        else:
            dset[max([0, coord0_vec[0]]):min([whole_object_size[0], coord0_vec[-1] + 1]), ind_old] += vals_delta
    return


def output_object(obj, shared_file_object, output_folder, unknown_type='delta_beta',
                  full_output=True, ds_level=1, i_epoch=0, i_batch=0, save_history=True):

    if shared_file_object:
        obj0 = obj.dset[:, :, :, 0]
        obj1 = obj.dset[:, :, :, 1]
    else:
        obj0 = w.to_numpy(obj.delta)
        obj1 = w.to_numpy(obj.beta)

    if unknown_type == 'delta_beta':
        if full_output:
            fname0 = 'delta_ds_{}'.format(ds_level)
            fname1 = 'beta_ds_{}'.format(ds_level)
        else:
            if save_history:
                fname0 = 'delta_{}_{}'.format(i_epoch, i_batch)
            else:
                fname0 = 'delta'
        dxchange.write_tiff(obj0, os.path.join(output_folder, fname0), dtype='float32', overwrite=True)
        if full_output:
            dxchange.write_tiff(obj1, os.path.join(output_folder, fname1), dtype='float32', overwrite=True)

    elif unknown_type == 'real_imag':
        if full_output:
            fname0 = 'obj_mag_ds_{}'.format(ds_level)
            fname1 = 'obj_phase_ds_{}'.format(ds_level)
        else:
            if save_history:
                fname0 = 'obj_mag_{}_{}'.format(i_epoch, i_batch)
                fname1 = 'obj_phase_{}_{}'.format(i_epoch, i_batch)
            else:
                fname0 = 'obj_mag'
                fname1 = 'obj_phase'
        dxchange.write_tiff(np.sqrt(obj0 ** 2 + obj1 ** 2), os.path.join(output_folder, fname0), dtype='float32', overwrite=True)
        dxchange.write_tiff(np.arctan2(obj1, obj0), os.path.join(output_folder, fname1), dtype='float32', overwrite=True)


def output_probe(probe_real, probe_imag, output_folder,
                  full_output=True, ds_level=1, i_epoch=0, i_batch=0, save_history=True):

    probe_real = w.to_numpy(probe_real)
    probe_imag = w.to_numpy(probe_imag)
    if full_output:
        fname0 = 'probe_mag_ds_{}'.format(ds_level)
        fname1 = 'probe_phase_ds_{}'.format(ds_level)
    else:
        if save_history:
            fname0 = 'probe_mag_{}_{}'.format(i_epoch, i_batch)
            fname1 = 'probe_phase_{}_{}'.format(i_epoch, i_batch)
        else:
            fname0 = 'probe_mag'.format(i_epoch, i_batch)
            fname1 = 'probe_phase'.format(i_epoch, i_batch)
    dxchange.write_tiff(np.sqrt(probe_real ** 2 + probe_imag ** 2),
                        fname=os.path.join(output_folder, fname0), dtype='float32', overwrite=True)
    dxchange.write_tiff(np.arctan2(probe_imag, probe_real),
                        fname=os.path.join(output_folder, fname1), dtype='float32', overwrite=True)


def get_subdividing_params(image_shape, n_blocks_y, n_blocks_x, **kwargs):
    """
    Calculate block arrangement and locations when a large 2D image is to be divided into square sub-blocks.
    :param image_shape: shape of original image.
    :param n_blocks: total number of blocks.
    :param safe_zone_width: overlapping length between adjacent blocks. If None, estimate using the sqrt(lambda * z) rule.
    :return: An array of [n_blocks, 4].
    """

    # Must satisfy:
    # 1. n_block_x * n_block_y = n_blocks
    # 2. block_size * n_block_y = wave_shape[0]
    # 3. block_size * n_block_x = wave_shape[1]
    n_blocks = n_blocks_x * n_blocks_y
    block_size_y, block_size_x = np.ceil([image_shape[0] / n_blocks_y, image_shape[1] / n_blocks_x]).astype(int)
    if rank == 0:
        print('n_blocks_y: ', n_blocks_y)
        print('n_blocks_x: ', n_blocks_x)
        print('n_blocks: ', n_blocks)
        print('block_size: ', block_size_y, block_size_x)

    block_range_ls = np.zeros([n_blocks, 4])
    for i_pos in range(n_blocks):
        line_st = i_pos // n_blocks_x * block_size_y
        line_end = line_st + block_size_y
        px_st = i_pos % n_blocks_x * block_size_x
        px_end = px_st + block_size_x
        block_range_ls[i_pos, :] = np.array([line_st, line_end, px_st, px_end])
    return block_range_ls.astype(int)


def subdivide_image(img, block_range_ls, override_backend=None):

    block_size_sz_y, block_size_sz_x = (block_range_ls[0][1] - block_range_ls[0][0], block_range_ls[1][1] - block_range_ls[1][0])
    img, pad_arr = pad_object(img, img.shape, block_range_ls[:, 0:3:2], [block_size_sz_y, block_size_sz_x], mode='edge', override_backend=override_backend)
    block_ls = []
    for line_st, line_end, px_st, px_end in block_range_ls:
        line_st += pad_arr[0, 0]
        line_end += pad_arr[0, 0]
        px_st += pad_arr[1, 0]
        px_end += pad_arr[1, 0]
        block_ls.append(img[line_st:line_end, px_st:px_end])
    return block_ls