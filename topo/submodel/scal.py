import param

import imagen

from topo.base.arrayutil import DivideWithConstant
from topo import sheet

from . import Model
from .gcal import ModelGCAL


@Model.definition
class ModelSCAL(ModelGCAL):
    """
    Spatially-tuned GCAL (SCAL) calibrated to represent a 3 degree
    parafoveal region of macaque primary visual cortex, assuming a
    3 mm/deg magnification factor and 1 mm orientation hypercolumn
    distance.

    Changes from ModelGCAL include relative strengths, homeostatic
    sparsity constraints, connection radii and switching from
    subtractive to divisive inhibition. The explanation of the
    calibration process is explained in a forthcoming notebook.
    """

    num_inputs = param.Number(default=1.0)

    aff_strength = param.Number(default=3.2, bounds=(0.0, None), doc="""
        Overall strength of the afferent projection to V1.""")

    exc_strength = param.Number(default=2.6, bounds=(0.0, None), doc="""
        Overall strength of the lateral excitatory projection to V1.""")

    inh_strength = param.Number(default=4.5, bounds=(0.0, None), doc="""
        Overall strength of the lateral inhibitory projection to V1.""")

    t_init = param.Number(default=0.35, doc="""
        The initial threshold value for homeostatic adaptation in V1.""")

    target_activity = param.Number(default=0.016, doc="""
        The target activity for homeostatic adaptation in V1,""")

    #=================#
    # Spatial extents #
    #=================#

    latexc_radius = param.Number(default=0.1, bounds=(0, None), doc="""
        Radius of the lateral excitatory bounds within V1.""")

    latinh_radius = param.Number(default=0.22917, bounds=(0, None), doc="""
        Radius of the lateral inhibitory bounds within V1.""")

    latexc_size = param.Number(default=0.087, bounds=(0, None), doc="""
        Size of the lateral excitatory connections within V1.""")

    latinh_size = param.Number(default=0.16, bounds=(0, None), doc="""
        Size of the lateral inhibitory connections within V1.""")

    v1aff_radius = param.Number(default=0.5, bounds=(0, None), doc="""
        Connection field radius of a unit in V1 to units in a LGN
        sheet.""")

    center_size = param.Number(default=0.2, bounds=(0, None), doc="""
        The size of the central Gaussian used to compute the
        center-surround receptive field.""")

    surround_size = param.Number(default=4 * 0.2, bounds=(0, None), doc="""
        The size of the surround Gaussian used to compute the
        center-surround receptive field.""")

    gain_control_size = param.Number(default=0.8, bounds=(0, None), doc="""
        The size of the divisive inhibitory suppressive field used for
        contrast-gain control in the LGN sheets. This also acts as the
        corresponding bounds radius.""")

    lgnaff_radius = param.Number(default=0.4, bounds=(0, None), doc="""
        Connection field radius of a unit in the LGN level to units in
        a retina sheet.""")

    lgnlateral_radius = param.Number(default=0.4, bounds=(0, None), doc="""
        Connection field radius of a unit in the LGN level to
        surrounding units, in case gain control is used.""")

    #=====================#
    # Divisive inhibition #
    #=====================#

    division_constant = param.Number(default=1.0, doc="""
        The constant offset on the denominator for divisive lateral
        inhibition to avoid divide-by-zero errors:

        divide(x,maximum(y,0) + division_constant).""")


    def training_pattern_setup(self, **overrides):
        or_dim = 'or' in self.dims
        gaussian = self.dataset == 'Gaussian'
        pattern_parameters = {'size': 0.2 if or_dim and gaussian else 3 * 0.2 if gaussian else 10.0,
                              'aspect_ratio': 4.66667 if or_dim else 1.0,
                              'scale': self.contrast / 100.0}
        return super(ModelSCAL, self).training_pattern_setup(
            pattern_parameters=pattern_parameters)


    @Model.CFProjection
    def lateral_inhibitory(self, src_properties, dest_properties):
        return Model.CFProjection.params(
            delay=0.05,
            name='LateralInhibitory',
            weights_generator=imagen.random.GaussianCloud(gaussian_size=self.latinh_size),
            strength=self.inh_strength,
            activity_group=(0.6, DivideWithConstant(c=self.division_constant)),
            learning_rate=self.inh_lr,
            nominal_bounds_template=sheet.BoundingBox(radius=self.latinh_radius))


    def analysis_setup(self):
        super(ModelSCAL, self).analysis_setup()
        from topo.analysis.command import measure_sine_pref

        sf_relative_sizes = [self.sf_spacing ** (sf_channel - 1) for sf_channel
                             in self['SF']]
        wide_relative_sizes = [0.5 * sf_relative_sizes[0]] +\
                              sf_relative_sizes + [2.0 * sf_relative_sizes[-1]]
        relative_sizes = (wide_relative_sizes if self.expand_sf_test_range else sf_relative_sizes)
        measure_sine_pref.frequencies = [1.65 * s for s in relative_sizes]