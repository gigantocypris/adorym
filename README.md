# Adorym: Automatic Differentiation-based Object Reconstruction with DynaMical Scattering

## Installation
Get this repository to your hard drive using 
```
git clone https://github.com/mdw771/adorym
```
and then use PIP to build and install:
```
pip install ./adorym
```

If you will modify internal functions of Adorym, *e.g.*, add new forward
models or refinable parameters, it is suggested to use the `-e` flag to
enable editable mode so that you don't need to rebuild Adorym each time
you make changes to the source code:
```
pip install -e ./adorym
```

After installation, type `python` to open a Python console, and check
the installation status using `import adorym`. If an `ImportError` occurs,
you may need to manually install the missing dependencies. Most
dependencies are available on PIP and can be acquired with
```
pip install <package_name>
```
or through Conda if you use the Anaconda or Miniconda distribution of Python:
```
conda install <package_name>
```
In order to run Adorym using PyTorch backend with GPU support, please
make sure the right version of PyTorch that matches your CUDA version
is installed. The latter can be checked through `nvidia-smi`.

## Quick start guide
Adorym does 2D/3D ptychography, CDI, holography, and tomography all
using the `reconstruct_ptychography` function in `ptychography.py`.
You can make use of the template scripts in `demos` or `tests` to start
your reconstruction job.

### Running a demo script
Adorym comes with a few datasets and scripts for demonstration and testing,
but the raw data files of some of them are stored elsewhere due to the size limit
on GitHub. If the folder in `demos` or `tests` corresponding to a
certain demo dataset
contains only a text file named `raw_data_url.txt`, please download the
dataset at the URL indicated in the file.

On your workstation, open a terminal in the `demos` folder in Adorym's
root directory, and run the desired script -- say, `multislice_ptycho_256_theta.py`,
which will start a multislice ptychotomography reconstruction job that
solves for the 256x256x256 "cone" object demonstrated in the paper
(see *Publications*), with
```
python multislice_ptycho_256_theta.py
```
To run the script with multiple processes, use
```
mpirun -n <num_procs> python multislice_ptycho_256_theta.py
```

### Dataset format
Adorym reads raw data contained an HDF5 file. The diffraction images should be
stored in the `exchange/data` dataset as a 4D array, with a shape of
`[n_rotation_angles, n_diffraction_spots, image_size_y, image_size_x]`.
In a large part, Adorym is blind to the type of experiment, which means
there no need to explicitly tell it the imaging technique used to generate
the dataset. For imaging data collected from only one angle, `n_rotation_angles = 1`.
For full-field imaging without scanning, `n_diffraction_spots = 1`. For
2D imaging, set the last dimension of the object size to 1 (this will be
introduced further below).

Experimental metadata including beam energy, probe position, and pixel
size, may also be stored in the HDF5, but they can also be provided individually
as arguments to the function `reconstruct_ptychography`. When these arguments
are provided, Adorym uses the arguments rather than reads the metadata from
the HDF5.

The following is the full structure of the HDf5:
```
data.h5
  |___ exchange
  |       |___ data: float, 4D array
  |                  [n_rotation_angles, n_diffraction_spots, image_size_y, image_size_x]
  |
  |___ metadata
          |___ energy_ev: scalar, float. Beam energy in eV
          |___ probe_pos_px: float, [n_diffraction_spots, 2]. 
          |                  Probe positions (y, x) in pixel.
          |___ psize_cm: scalar, float. Sample-plane pixel size in cm.
          |___ free_prop_cm: (optional) scalar or array 
          |                  Distance between sample exiting plane and detector.
          |                  For far-field propagation, do not include this item. 
          |___ slice_pos_cm: (optional) float, 1D array
                             Position of each slice in sparse multislice ptychography. Starts from 0.
```

### Parameter settings
The scripts in `demos` and `tests` supply the `reconstruct_ptychography`
with parameters listed as a Python dictionary. You may find the docstrings
of the function helpful, but here lists a collection of the most crucial
parameters:

#### Backend

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`backend`|String|`autograd`|Select `'pytorch'` or `'autograd'`. Both can be used as the automatic differentiation engine, but only the PyTorch backend supports GPU computation. Some features are only supported through PyTorch, including affine transformation refinement and object tilt-angle refinement. |

#### Raw data and experimental parameters

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`fname`|String| |Name of the HDF5 containing raw data. Put only the basename here; any path predix should go to `save_path`. Some features are only supported through PyTorch, including affine transformation refinement and object tilt-angle refinement. |
|`obj_size`|Array of Int| |`[L_y, L_x, L_z]`. The size of the object function (*i.e.*, the unknowns) in pixels. `L_y` is the size in the vertical direction, while `L_x` and `L_z` refer to sizes on the horizontal plane. For 2D reconstruction, set `L_z` to 1. For 3D reconstruction, it is strongly recommended to keep `L_x == L_z`. For doing sparsely spaced multislice tomography (*i.e.*, when the number of slices along beam axis is much less than the number of lateral pixels), the best practice is to set `binning` to a larger value, instead of using a small `L_z`.
|`probe_pos`|Array of Float|`None`|`[n_diffraction_spots, 2]`. Probe positions in a scanning-type experiment in pixel in the object frame (*i.e.*, real-unit probe positions divided by sample plane pixel size). Default to `None`. If `None`, the program will attempt to get the value from HDF5. The positions will be interpreted as the **top-left corner of the probe array in object frame**. For single-shot experiments, set `probe_pos` to `[[0, 0]]`.
|`theta_st`|Float|0|Starting rotation angle in radian. Default to 0.
|`theta_end`|Float|`PI`|End rotation angle in radian. For single angle data, set this the same as `theta_st`.
|`n_theta`|Int|`None`|Number of rotation angles. If `None`, the number will be inferred from the shape of the HDF5 dataset. 
|`theta_downsample`|Int|`None`|By how many times the raw data should be downsampled in rotation angles. 
|`energy_ev`|Float|`None`|X-ray beam energy in eV. If `None`, the program will attempt to get the value from HDF5.
|`psize_cm`|Float|`None`|**Lateral** pixel size at sample plane in cm. If `None`, the program will attempt to get the value from HDF5. If axial pixel size is different, use `slice_pos_cm`.
|`free_prop_cm`|Float|`None`|The distance between sample and detector in cm. For far-field imaging, set it to `None` or `'inf'`, so that the programs uses Fraunhofer approximation. **For near-field imaging, this value is assumed to be the propagation distance in a plane-wave illuminated experiment; if the illumination is a spherical wave generated by a point source, use the effective distance given by Fresnel scaling theorem: `z_eff = z1 * z2 / (z1 + z2)`**.
|`raw_data_type`|String|`'intensity'`|Choose from `'intensity'` or `'magnitude'`. This informs the optimizer the type of raw data contained in the HDF5, and determines whether the measured data should be square-rooted when calculating loss. **For conventional tomography with `pure_propjection=True` and `is_minus_logged=True`, this must be `magnitude`!**
|`is_minus_logged`|Boolean|`False`|Whether the raw projection data have been minus-logged. This is usually used in conventional tomography. If `True`, forward model will return a simple summation of `beta` along the beam axis.
|`slice_pos_cm`|Array of Float|`None`|Position of each slice in sparse multislice ptychography. Starts from 0. If `None`, the program will attempt to get the value from HDF5.

#### Reconstruction parameters

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`n_epochs`|Int|`'auto'`|Number of epochs to run. An epoch refers to a cycle during which all diffraction data are processed. Set it to `'auto'` to automatically stops the reconstruction when the reduction rate of loss falls below `crit_conv_rate`. **This option is not recommended especially for noisy data due to the possibility of fake positives.** The best practice so far is to set `n_epochs` to a sufficiently large value and observe the loss curve and reconstruction output until satisfactory results are obtained.
|`crit_conv_rate`|Float|0.03|If the reduction rate of loss at the current epoch in regards to the previous one is below this value, convergence is assumed to be reached and the reconstruction process stops.
|`max_epochs`|Int|200|When `n_epochs` is set to `'auto'`, the program will stop regardless of the loss reduction rate once this number of epochs have been run.
|`alpha_d`|Float|0|Weight applied to l1-norm of the delta (or real) part of the object function, depending on the setting of `unknown_type`. The full loss function is in the form of `L = D(f(x), y0) + alpha_d * |x_d|_1 + alpha_b * |x_b|_1 + gamma * TV(x)`. 
|`alpha_b`|Float|0|Weight applied to l1-norm of the beta (or imaginary) part of the object function.
|`gamma`|Float|0|Weight applied to total variation of the object function.
|`minibatch_size`|Int|1|The number of diffraction spots to be processed at a time. When multi-processing, this is the number of diffraction spots processed by each rank.
|`multiscale_level`|Int|1|Number of levels for multi-scale progressive reconstruction. *This feature is still experimental.* 
|`n_epoch_final_pass`|Int|None|If `multiscale_level` is larger than 1, this parameter sets the number of epochs for the last (full-resolution) pass.
|`initial_guess`|List of Arrays|None|The initial guess of the object function in the form of `[obj_delta, obj_beta]` when `unknown_type` is `delta_beta`, or `[obj_mag, obj_phase]` when `unknown_type` is `real_imag`. The arrays must have the same size as specified by `obj_size`.
|`random_guess_means_sigmas`|List of Floats|`(8.7e-7, 5.1e-8, 1e-7, 1e-8)`|When `initial_guess` is `None`, the object function will be initialized usin Gaussian randoms. This argument provides the Gaussian parameters in the format of `(mean_delta, mean_beta, sigma_delta, sigma_beta)` or `(mean_mag, mean_phase, sigma_mag, sigma_phase)`, depending on the setting of `unknwon_type`.
|`n_batch_per_update`|Int|1|The number of minibatches to accumulate before the object is updated. Ignored when `update_scheme` is `per angle`.
|`reweighted_l1`|Bool|`False`|If `True` and `alpha_d != 0`, the program uses reweighted l1-norm to regularize the object (see Candès, E. J., Wakin,  M. B. & Boyd, S. P. Enhancing Sparsity by Reweighted ℓ1 Minimization. *Journal of Fourier Analysis and Applications* **14**, (2008). )
|`interpolation`|String|`'bilinear'`|Interpolation method for rotation.
|`update_scheme`|String|`'immediate'`|Choose from `'immediate'` or `'per angle'`. If `'immediate'`, the object function is updated immedaitely after each minibatch is done. If `'per angle'`, updated is performed only after all diffraction patterns from the current rotation angle are processed. If `shared_file_object` is on, the `'per angle'` mode is used regardless of this setting.
|`unknown_type`|String|`'delta_beta'`|Choose from `delta_beta` and `real_imag`. If set to `delta_beta`, the program treats the unknowns as the delta and beta parts in the complex refractive indices of the object, `n = 1-delta-i*beta`. In this case, modulation to the wavefield by each slice of the object will be done as `wavefield * exp(-i*k*n*z)`. If set to `real_imag`, the unknowns are treated as the real and imaginary part of a multiplicative object function, where the modulation is done as `wavefield * (obj_real + i * obj_imag)`. Using `delta_beta` can help overcome mild phase wrapping, while using `real_imag` generally leads to better numerical robustness.
|`randomize_probe_pos`|Bool|False|Whether to randomize diffraction spots on each viewing angle when there are more than 1 of them. Recommended to be `True` for 2D ptychography.
|`common_probe_pos`|Bool|True|Whether the number and position of tiles are the same for all viewing angles. If `False`, the tile positions for each angle should be provided in the HDF5 as 'metadata/probe_pos_px_<i_theta>'. The main dataset remains as a 4D array, where the size of the second axis is determined by the angle that has the most tiles. 

#### Object optimizer options
| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`optimize_object`|Bool|`True`|Keep True in most cases. Setting to False forbids the object from being updated using gradients, which might be desirable when you just want to refine parameters for other reconstruction algorithms. 
|`optimizer`|String|`'adam'`|Optimizer type for updating the object function. Choosen from `'adam'` or `'gd'` (steepest gradient descent). You may also try `'curveball'` but it is still experimental and supports only data parallelism mode. 
|`learning_rate`|Float|`1e-5`|Learning rate, or step size of the chosen optimizer for the object function. Ignored if `optimizer` is `'curveball'`.

#### Finite support constraint

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`finite_support_mask_path`|String|`None`|The path to the TIFF file storing the finite support mask. In general, this is needed only for single-shot CDI and holography.
|`shrink_cycle`|Int|`None`|For every how many minibatches should the finite support mask be shrink-wrapped. Use `None` to disable shrink-wrap. Useful only when `finite_support_mask_path` is not None.
|`'shrink_threshold'`|Float|`1e-9`|Threshold for shrink-wrapping. Useful only when `finite_support_mask_path` is not None.

#### Object contraints

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`object_type`|String|`'normal'`|Choose from `'normal'`, `'phase_only'`, or `'absorption_only'`. If `'absorption_only'`, the delta part of the phase of the object will be forced to be 0 after each update. Vice versa for `'phase_only'`.
|`non_negativity`|Bool|`False`|Whether to enforce non-negative constraint. Useful only when `unknown_type` is `delta_beta`.

#### Forward model

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ---------- | ----------------|
|`forward_model`|`'auto'` or `adorym.ForwardModel` class|`'auto'`|Forward model class. Use `'auto'` to let the program automatically determine forward model from other parameters. 
|`forward_algorithm`|String|`'fresnel''`|Choose from `'fresnel'` and `'ctf'`.
|`ctf_lg_kappa`|Float|1.7|The natural log of the proportional coefficient between `delta` and `beta`, *i.e.*, `kappa = 10 ** ctf_lg_kappa; beta_slice = delta_slice * kappa`. Only useful when `optimize_ctf_lg_kappa` is `True`, in which case the object will be constrained to be homogeneous. Otherwise, `delta` and `beta` are reconstructed independently and this argument is ignored.
|`binning`|Int|1|The number of axial slices to be binned (*i.e.*, to be treated as line integrals) during multislice propagation.
|`pure_projection`|Bool|`False`|Set to `True` to model the propagation through the entire object as a simple line projection, not using multislice at all.
|`two_d_mode`|Bool|`False`|If the HDF5 dataset contains multiple viewing angles (*i.e.*, the length of the first dimension is larger than 1), setting `two_d_mode` to `True` will let the program to treat it as a single-angle dataset, with the only angle being the first one. Set to `True` automatically if the last dimension of `obj_size` is 1.
|`probe_type`|String|`'gaussian'`|Choose from `'gaussian'`, `'plane'`, `'ifft'`, `'aperture_defocus'`, and `'supplied'`. The method of initializing the probe function. Some options requires additional inputs from user. For more details, see table below.
|`probe_extra_defocus_cm`|Float|`None`|If not `None`, the probe will be defocused further by the specified distance in cm.
|`n_probe_modes`|Int|1|Number of probe modes.
|`rescale_probe_intensity`|Bool|`True`|Scale the probe function so that its integrated power spectrum (related to the total number of photons) matches that of the raw data.
|`loss_function_type`|String|`'lsq'`|Choose from `'lsq'` or `'poisson'`. Whether to use a least square term or a Poisson maximum likelihood term to measure the mismatch of predicted intensity.
|`poisson_multiplier`|Float|1|Intensity scaling factor in Poisson loss function. If intensity data is normalized, this should be the average number of incident photons per pixel.
|`safe_zone_width`|Int|`None`|If not `None`, the object and probe tiles will be enlarged (through either selecting a larger area or padding) before propagation, and the enlarged parts are discarded after propagation. 
|`scale_ri_by_k`|Bool|`True`|Whether to add in the factor `k = 2*pi/lambda` when evaluating `exp(-iknz)`. Setting this argument to `False` may help fix numnerical instability problems.
|`sign_convention`|Int|1|Choose from 1 and -1. Determines whether to use the `exp(ikz)` convention or `exp(-ikz)` convention. The reconstructed phase in these two cases will be numerically inverted to each other. 

| **Value of `probe_type`** | **Options** |  **Description** |
| ------------------------- | ----------- | -----------------|
|`'gaussian'`|`probe_mag_sigma`, `probe_phase_sigma`, `probe_phase_max`|Initialize with a Gaussian probe. The Gaussian spreads, or the `*sigma` values, are in pixel. Magnitude max is 1 by default. 
|`'aperture_defocus'`|`aperture_radius`, `beamstop_radius`, `probe_defocus_cm`|Initialize the probe by defocuing an aperture function. All radii are in pixels (on the object frame). A circular aperture (if `beamstop_radius == 0`) or a ring aperture (if `0 < beamstop_radius < aperture_radius`) is generated and then Fresnel defocused to created the initial probe. 
|`'ifft'`| |Initialize the probe by taking the average of all diffraction patterns, performing an IFFT, and take the moduli.
|`'supplied'`|`probe_initial`|Provide a List of Arrays: `[probe_mag, probe_phase]`. If there are multiple probe modes, each of the arrays should be of shape `[n_probe_modes, len_probe_y, len_probe_x]`.

#### I/O

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ----------- | ----------------|
|`save_path`|String|`'.'`|Directory that contains the raw data HDF5. If it is in the same folder as the execution script, put `'.'`.
|`output_folder`|String|`None`|Name of the folder to place output data. The folder will be assumed to be under `save_path`, *i.e.*, the actual output directory will be `<save_path>/<output_folder>`. If `None`, the folder name will be automatically generated.
|`save_intermediate`|Bool|`False`|Whether to save the intermediate object (and probe when `optimize_probe` is `True`) after each minibatch.
|`save_history`|Bool|`False`|Useful only if `save_intermediate` is on, If `True`, the intermediate output will be saved with a different file name characterized by the current epoch and minibatch number. Otherwise, the intermediate output will be overwritten.
|`store_checkpoint`|Bool|`True`|Whether to save a checkpoint of the optimizable variables before each minibatch.
|`use_checkpoint`|Bool|`True`|If set to `True`, the program initializes the object and/or probe using the checkpoint stored in previous runs. If `False` or if checkpoint file is not found, start the reconstruction from scratch.
|`force_to_use_checkpoint`|Bool|`False`|If set to `True`, when previous checkpoint does not exist or is incomplete, the program raises an error instead of starting from scratch.
|`n_batch_per_checkpoint`|Int|10|For every how many minibatches should the checkpoint be updated. Large object functions may cause long writing overhead so a larger setting is preferred.
|`save_stdout`|Bool|`False`|Set to `True` to save the output messages as a text file.

#### Performance

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ----------- | ----------------|
|`cpu_only`|Boolean|`False`|Set to `False` to enable GPU. This option is ineffective when `backend` is `autograd`.
|`gpu_index`|Int|0|Index of GPU to use. To use multiple GPUs with multiple MPI ranks, make sure each rank is assigned with a different GPU.
|`n_dp_batch`|Int|20|Number of tiles to be **propagated** each time. Values larger than `minibatch_size` make no difference from setting it equal to `minibatch_size`. 
|`distribution_mode`|String or `None`|None|Choose from `None`, `'distributed_object'`, and `'shared_file'`, which respectively correspond to data parallel mode, distributed object mode, and H5-mediated low-memory mode. *Using the low-memory node requires H5Py built against MPIO-enabled HDF5.*
|`dist_mode_n_batch_per_update`|Int or `None`|None|Update frequency when using distributed object mode. If None, object is updated only after all DPs on an angle are processed.
|`precalculate_rotation_coords`|Bool|`True`|Whether to calculate rotation transformation coordinates and save them on the hard drive, or calculate them on-the-fly. 
|`rotate_out_of_loop`|Bool|`False`|Applies to simple data parallelism mode only. If True, DP will do rotation outside the loss function and the rotated object function is sent for differentiation. May reduce the number of rotation operations if minibatch_size < n_tiles_per_angle, but object can be updated once only after all tiles on an angle are processed. Also this will save the object-sized gradient array in GPU memory or RAM depending on current device setting.

#### Other (non-object) optimizers

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ----------- | ----------------|
|`optimize_probe`|Bool|`False`|Whether to optimize the probe function.
|`probe_learning_rate`|Float|`1e-5`|Probe optimization step size.
|`optimize_probe_defocuing`|Bool|`False`|Whether to optimize the defocusing distance of the probe.
|`probe_defocusing_learning_rate`|Float|`1e-5`|Probe defocusing optimization step size.
|`optimize_probe_pos_offset`|Bool|`False`|Whether to optimize the offset to probe positions. This is intended to correct for the x-y drifting of the sample stage at different angles. When turned on, the program creates an array with shape `[n_rotation_angles, 2]`. When processing data from a certain viewing angle, the positions of all diffraction spots are shifted by the value corresponding to that angle. The offset array is optimized by the optimizer along with the object function.
|`probe_pos_offset_learning_rate`|Float|`1e-2`|Probe offset overlap.
|`optimize_all_probe_pos`|Bool|`False`|Whether to optimize the probe positions at all angles. When turned on, the optimizer tries to optimize an array with shape `[n_rotation_angles, n_diffraction_spots, 2]`, which stores the correction values applied to each probe position at all viewing angles. Not recommended for ptychotomography with many viewing angles as it significantly increases the unknwon space to be searched, making the problem less well constrained.
|`all_probe_pos_learning_rate`|Float|`1e-2`|All probe position optimization step size.
|`optimize_slice_pos`|Bool|`False`|Whether to optimize slice positions. Used for sparse multislice ptychography where slice spacings are not uniform.
|`slice_pos_learning_rate`|Float|`1e-4`|Slice position optimization step size.
|`optimize_free_prop`|Bool|`False`|Whether to optimize free propagation distances.
|`free_prop_learning_rate`|Float|`1e-2`|Free propagation distance optimization step size. 
|`optimize_prj_affine`|Bool|`False`|Whether to optimize the affine alignment of holograms. Used for multi-distance holography.
|`prj_affine_learning_rate`|Float|`1e-3`|Affine alignment step size.
|`optimize_tilt`|Bool|`False`|Whether to optimize object tilt in all 3 axes. Works only with data parallelism mode.
|`tilt_learning_rate`|Float|`1e-3`|Tilt optimization step size. 
|`optimize_ctf_lg_kappa`|Bool|`False`|Whether to *enable homogeneity constraint* and optimize coefficient `kappa`, where `beta_slice = delta_slice * kappa`. 
|`ctf_lg_kappa_learning_rate`|Float|`1e-3`|`kappa` optimization step size. 
|`other_params_update_delay`|Int|0|If larger than 0, updates of above parameters will not happen until the specified number of minibatches are finished. This setting does not apply to object function.  

#### Other settings

| **Arg name** | **Type** | **Default** | **Description** |
| ------------ | -------- | ----------- | ----------------|
|`dynamic_rate`|Bool|`True`|Whether to adaptively reduce step size when using GD optimizer.
|`debug`|Bool|`False`|Whether to enable debugging messages. 
|`t_max_min`|Float or `None`|None|At the end of a batch, terminate the program with s tatus 0 if total time exceeds the set value. Useful for working with supercomputers' job dependency system, where the dependent may start only if the parent job exits with status 0.

### Output
During runtime, Adorym may create a folder named
`arrsize_?_?_?_ntheta_?` in the current working directory, which saves
the precalculated coordinates for rotation transformation. Other than
that, all outputs will be written in `<save_path>/<output_folder>`,
which is organized as shown in the chart below:
```
output_folder
     |___ convergence
     |         |___ loss_rank_0.txt // Record of the loss value after 
     |         |___ loss_rank_1.txt // each update coming from each process.
     |         |___ ...
     |___ intermediate
     |         |___ object
     |         |       |___ obj_mag(delta)_0_0.tiff
     |         |       |___ obj_phase(beta)_0_0.tiff
     |         |       |___ ...
     |         |___ probe
     |         |       |___ probe_mag_0_0.tiff
     |         |       |___ probe_phase_0_0.tiff
     |         |       |___ ...
     |         |___ probe_pos (if optimize_all_probe_pos is True)
     |         |       |___ probe_pos_correction_0_0_0.txt
     |         |       |___ ...
     |         ...
     |___ obj_delta_ds_1.tiff (or obj_mag_ds_1.tiff)
     |___ obj_beta_ds_1.tiff (or obj_phase_ds_1.tiff)
     |___ probe_mag_ds_1.tiff
     |___ probe_phase_ds_1.tiff
     |___ summary.txt // Summary of parameter settings.
     |___ checkpoint.txt // Exists if store_checkpoint is True.
     |___ obj_checkpoint.npy // Exists if store_checkpoint is True.
     |___ opt_params_checkpoint.npy // Exists if store_checkpoint is True and optimizer has parameters.
```
By default, all image outputs are in 32-bit floating points which can be
opened and viewed with ImageJ.

## Customization

### Adding your own forward model

You can create additional forward models beyond the existing ones. To begin with, in `adorym/forward_model.py`, 
create a class inheriting `ForwardModel` (*i.e.*, `class MyNovelModel(ForwardModel)`). Each forward model class 
should contain three essential methods: `predict`, `get_data`, `loss`, and `get_loss_function`. `predict` maps input variables
to predicted quantities (usually the real-numbered magnitude of the detected wavefield). `get_data` reads from
the HDF5 file the raw data corresponding to the minibatch currently being processed. `loss` is the last-layer
loss node that computes the (regularized)
loss values from the predicted data and the experimental measurement for the current minibatch. `get_loss_function`
concatenates the above methods and return the end-to-end loss function. If your `predict` returns the real-numbered
magnitude of the detected wavefield, you can use `loss` inherented from the parent class, although you still need to
make a copy of `get_loss_function` and explicitly change its arguments to match those of `predict` (do not use
implicit argument tuples or dictionaries like `*args` and `**kwargs`, as that won't work with Autograd!). If your `predict`
returns something else, you may also need to override `loss`. Also make sure your new forward model class contains
a `self.argument_ls` attribute, which should be a list of argument strings that exactly matches the signature of `predict`.

To use your forward model, pass your forward model class to the `forward_model` argument of `reconstruct_ptychography`.
For example, in the script that you execute with Python, do the following:
```
import adorym
from adorym.ptychography import reconstruct_ptychography

params = {'fname': 'data.h5', 
          ...
          'forward_model': adorym.MyNovelModel,
          ...}
```

### Adding refinable parameters

To add new refinable parameters, (at the current stage) you'll need to add them to the dictionary `optimizable_params`
in `adorym/ptychography.py`. An optimizer will be created for each refinable parameter in this dictionary 
by function `create_and_initialize_parameter_optimizers`
in `adorym/optimizers.py`. If the parameter requires a special rule when it is defined, updated, or outputted, 
you will also need to explicitly modify `create_and_initialize_parameter_optimizers`, `update_parameters`,
`create_parameter_output_folders`, and `output_intermediate_parameters`.

## Publications
The early version of Adorym, which was used to demonstrate 3D reconstruction of continuous object beyond the depth of focus, is published as

Du, M., Nashed, Y. S. G., Kandel, S., Gürsoy, D. & Jacobsen, C. Three
dimensions, two microscopes, one code: Automatic differentiation for
x-ray nanotomography beyond the depth of focus limit. *Sci Adv* **6**,
eaay3700 (2020).
