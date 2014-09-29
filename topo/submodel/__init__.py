"""
A set of tools which allow specifying a model consisting of sheets
organized in levels, and projections connecting these sheets. The
sheets have an attribute matchconditions allowing to specify which
other (incoming) sheets a sheet should connect to.

Instances of the LabelDecorator decorator are offered for setting
parameters/matchconditions for a sheet within a level, as well as
parameters for projections.
"""

import itertools
from collections import defaultdict

import param
import lancet
import topo
import numbergen

from specifications import SheetSpec, ProjectionSpec, ModelSpec, ArraySpec # pyflakes:ignore (API import)
from dataviews.collector import AttrTree
from topo.misc.commandline import global_params



def select(index, *decorators):
    """
    A meta-decorator that applies one-of-N possible decorators based
    on the index. The index may be a boolean value for selecting
    between two options.

    """
    def wrapped(*args, **kwargs):
        return decorators[int(index)](*args, **kwargs)
    return wrapped



def order_projections(model, connection_order):
    """
    Helper function for reproducing random streams when
    time_dependent=False (where the order of projection instantiation
    matters). This function should only be used for legacy reasons and
    should not be used with new models.

    The first argument is an instance of Model (with projections
    configured) and the second is the projection ordering, specified
    by the (decorated) method names generating those projections.

    This function allows sorting on a single source sheet property. To
    specify such an ordering, use a tuple where the first element is
    the relevant method name and the second element is a source sheet
    property dictionary to match. For instance, specifying the
    connection_order list as:

    [('V1_afferent_projections', {'polarity':'On'}),
    ('V1_afferent_projections',  {'polarity':'Off'})]

    will order the 'On' projections generated by the
    V1_afferent_projections method before the 'Off' projections.
    """
    connection_list = [el if isinstance(el, tuple) else (el, None)
                       for el in connection_order]

    for spec in model.projections:
        matches = [(i, el) for i, el in enumerate(connection_list)
                   if el[0] == spec.matchname]
        if len(matches) == 0:
            raise Exception("Could not order projection %r" % spec)
        elif len(matches) == 1:
            (i, (k, v)) = matches[0]
            spec.sort_precedence = i
            continue

        property_keys = [pdict.keys() for (_, (_, pdict)) in matches]
        if not all(len(pkeys)==1 for pkeys in property_keys):
            raise Exception("Please specify only a single property to sort on")
        if not all(pkey[0]==property_keys[0][0] for pkey in property_keys):
            raise Exception("Please specify only a single property to sort on")

        key = property_keys[0][0]
        spec_property_value = spec.src.properties[key]
        match = [ind for (ind, (_, pdict)) in matches if pdict[key] == spec_property_value]
        if len(match) != 1:
            raise Exception("Could not order projection %r by property %r" % (spec, key))
        spec.sort_precedence = match[0]


class MatchConditions(object):
    """
    Decorator class for matchconditions.
    """

    def __call__(self, level, method_name):

        def decorator(f):
            f._matchcondition_level = level
            f._target_method_name = method_name
            return f
        return decorator

    def __repr__(self):
        return "MatchConditions()"


class ComponentDecorator(object):
    """
    Decorator class that can be instantiated with a component type to
    create a decorator used to associate methods with the
    corresponding component.

    This class works by setting a '_component_type' attribute on the
    decorated method. Methods that have been annotated in this way may
    be tracked in classes decorated with the ComponentRegistry class
    decorator.
    """
    def __init__(self, name, object_type):
        self.name = name
        self.type = object_type

        # Enable IPython tab completion in the settings method
        kwarg_string = ", ".join("%s=%s" % (name, type(p.default))
                                 for (name, p) in object_type.params().items())
        self.params.__func__.__doc__ =  'params(%s)' % kwarg_string


    def params(self, **kwargs):
        """
        A convenient way of generating parameter dictionaries with
        tab-completion in IPython.
        """
        return kwargs


    def __call__(self, f):
        f._component_type = self.type
        return f

    def __repr__(self):
        return "ComponentDecorator(%s, %s)" % (self.name, self.type.name)



class ComponentRegistry(object):
    """
    An instance of this class is to be used as class decorator. Any
    decorated class using ClassDecorators on their methods will be
    registered with their corresponding component types.
    """

    def __init__(self):
        self.method_registry = defaultdict(dict)
        self.type_registry = defaultdict(dict)
        self.matchconditions = []


    def compute_conditions(self, level, model, properties):
        """
        Collect the matchcondition dictionary for a particular level
        given a certain Model instance and sheet properties. If no
        matchconditions are available, an empty dictionary is
        returned.

        Respects the appropriate method resolution order (mro) of the
        given model instance.  """
        mro = [el.__name__ for el in model.__class__.mro()]
        filtered = [(target, fn) for (cls, lvl, target, fn) in self.matchconditions
                    if (cls in mro) and lvl == level]

        return dict((target, fn(model, properties)) for (target, fn) in filtered)


    def lookup(self, cls, name, mode):
        """
        Given a class, a method name and a mode, return either the
        component type (if mode='type) or the appropriate method (if
        mode='method').
        """
        mro = [el.__name__ for el in cls.mro()]
        registry = self.method_registry if mode=='method' else self.type_registry

        for class_name in mro:
            entries = registry[class_name]
            if name in entries:
                return entries[name]
        raise KeyError("Could not find method named %r."
                       " Please ensure classes using component decorators"
                       " are decorated with the Model.definition"
                       " class decorator." % name)

    def __call__(self, cls):
        for name, method in cls.__dict__.iteritems():
            class_name = cls.__name__
            component_type = getattr(method, "_component_type", False)
            if component_type:
                method_name = method.__name__
                self.method_registry[class_name].update({method_name:method})
                self.type_registry[class_name].update({method_name:component_type})

            match_level = getattr(method, '_matchcondition_level', False)
            target_method_name = getattr(method, '_target_method_name', False)
            if match_level and target_method_name:
                info = (class_name, match_level, target_method_name, method)
                self.matchconditions.append(info)
        return cls


# A singleton to be used by model class using decorators
definition = ComponentRegistry()


class Model(param.Parameterized):
    """
    The available setup options are:

        :'training_patterns': fills the training_patterns AttrTree
        with pattern generator instances. The path is the name of the
        input sheet. Usually calls PatternCoordinator to do this.
        :'sheets_setup': determines the number of sheets, their types
        and names sets sheet parameters according to the registered
        methods in level sets sheet matchconditions according to the
        registered methods in matchconditions
        :'projections': determines which connections should be present
        between the sheets according to the matchconditions of
        SheetSpec objects, using connect to specify the
        connection type and sets their parameters according to the
        registered methods in connect


    The available instantiate options are:

        :'sheets': instantiates all sheets and registers them in
        topo.sim
        :'projections': instantiates all projections and registers
        them in topo.sim
    """

    random_seed = param.Integer(default=None, doc="""
       Overrides the default global seed on param.random_seed when not None.""")

    __abstract = True

    # A convenient handle on the definition class decorator
    definition = definition

    matchconditions = MatchConditions()
    sheet_decorators = set()
    projection_decorators = set()

    @classmethod
    def register_decorator(cls, object_type):
        name = object_type.name
        decorator = ComponentDecorator(name, object_type)
        setattr(cls, name,  decorator)

        if issubclass(object_type, topo.sheet.Sheet):
            cls.sheet_decorators.add(decorator)
        if issubclass(object_type, topo.projection.Projection):
            cls.projection_decorators.add(decorator)


    @property
    def modified_parameters(self):
        "Dictionary of modified model parameters"
        return {k:v for k,v in self.get_param_values(onlychanged=True)}


    def __init__(self, register=True, time_dependent=True, **params):
        numbergen.TimeAware.time_dependent = time_dependent
        if register:
            self._register_global_params(params)
        super(Model,self).__init__(**params)

        self.specification = None
        self.properties = {}
        # Training patterns need to be accessed by GeneratorSheets
        self.properties['training_patterns'] = AttrTree()


    def _register_global_params(self, params):
        """
        Register the parameters of this object as global parameters
        available for users to set from the command line.  Values
        supplied as global parameters will override those of the given
        dictionary of params.
        """

        for name,obj in self.params().items():
            global_params.add(**{name:obj})

        for name,val in params.items():
            global_params.params(name).default=val

        params.update(global_params.get_param_values())
        params["name"]=self.name


    #==============================================#
    # Public methods to be implemented by modelers #
    #==============================================#

    def property_setup(self, properties):
        """
        Method to precompute any useful properties from the class
        parameters. For instance, if there is a ``num_lags``
        parameter, this method could compute the actual projection
        delays and store it as properties['lags']. The return value is
        the updated 'properties' dictionary.

        In addition, this method can be used to configure class
        attributes of the model components.
        """
        return properties


    def training_pattern_setup(self, **overrides):
        """
        Returns a dictionary of PatternGenerators to be added to
        self.training_patterns, with the target sheet name keys and
        pattern generator values.

        The overrides keywords can be used by a subclass to
        parameterize the training patterns e.g. override the default
        parameters of a PatternCoordinator object.
        """
        raise NotImplementedError


    def sheet_setup(self):
        """
        Returns a dictionary of properties or equivalent Lancet.Args
        object. Each outer key must be the level name and the values
        are lists of property dictionaries for the sheets at that
        level (or equivalent Lancet Args object). For instance, two
        LGN sheets at the 'LGN' level could be defined by either:

        {'LGN':[{'polarity':'ON'}, {'polarity':'OFF'}]}
        OR
        {'LGN':lancet.List('polarity', ['ON', 'OFF'])}

        The specified properties are used to initialize the sheets
        AttrTree with SheetSpec objects.
        """
        raise NotImplementedError


    def analysis_setup(self):
        """
        Set up appropriate defaults for analysis functions in
        topo.analysis.featureresponses.
        """
        pass


    #====================================================#
    # Remaining methods should not need to be overridden #
    #====================================================#

    def setup(self,setup_options=True):
        """
        This method can be used to setup certain parts of the
        submodel.  If setup_options=True, all setup methods are
        called.  setup_options can also be a list, whereas all list
        items of available_setup_options are accepted.

        Available setup options are:
        'training_patterns','sheets','projections' and 'analysis'.

        This method returns a ModelSpec object which is also set as
        the value of the 'specification' attribute.

        Please consult the docstring of the Model class for more
        information about each setup option.
        """
        available_setup_options = ['attributes',
                                   'training_patterns',
                                   'sheets',
                                   'projections',
                                   'analysis']

        if self.random_seed is not None:
            param.random_seed = self.random_seed

        if setup_options==True:
            setup_options = available_setup_options

        if 'attributes' in setup_options:
            self.properties = self.property_setup({})

        model = ModelSpec(self, self.properties)

        if 'training_patterns' in setup_options:
            training_patterns = self.training_pattern_setup()
            for name, training_pattern in training_patterns.items():
                model.training_patterns.set_path(name, training_pattern)

            self.properties['training_patterns'] = training_patterns
        if 'sheets' in setup_options:
            sheet_properties = self.sheet_setup()

            enumeration = enumerate(sheet_properties.items())
            for (ordering, (level, property_list)) in enumeration:
                sheet_type =  self.definition.lookup(self.__class__, level, mode='type')

                if isinstance(property_list, lancet.Identity):
                    property_list = [{}]
                elif isinstance(property_list, lancet.Args):
                    property_list = property_list.specs
                # If an empty list or Args()
                elif not property_list:
                    continue

                for properties in property_list:
                    spec_properties = dict(level=level, **properties)
                    sheet_spec = SheetSpec(sheet_type, spec_properties)
                    sheet_spec.sort_precedence = ordering
                    model.sheets.set_path(str(sheet_spec), sheet_spec)

            model = self._update_sheet_spec_parameters(model)

        if 'projections' in setup_options:
            model = self._compute_projection_specs(model)
        if 'analysis' in setup_options:
            self.analysis_setup()

        self.specification = model
        return model


    def _update_sheet_spec_parameters(self, model):
        for sheet_spec in model.sheets.path_items.values():
            param_method = self.definition.lookup(self.__class__, sheet_spec.level, 'method')
            if not param_method:
                raise Exception("Parameters for sheet level %r not specified" % sheet_spec.level)

            updated_params = param_method(self, sheet_spec.properties)
            sheet_spec.update(**updated_params)
        return model


    def _matchcondition_holds(self, matchconditions, src_sheet):
        """
        Given a dictionary of properties to match and a target sheet
        spec, return True if the matchcondition holds else False.
        """
        matches=True
        if matchconditions is None:
            return False

        for incoming_key, incoming_value in matchconditions.items():
            if (incoming_key in src_sheet.properties and \
                    str(src_sheet.properties[incoming_key]) != str(incoming_value)) \
                    or (incoming_key not in src_sheet.properties and incoming_value is not None):
                matches=False
                break

        return matches

    def _compute_projection_specs(self, model):
        """
        Loop through all possible combinations of SheetSpec objects in
        self.sheets If the src_sheet fulfills all criteria specified
        in dest_sheet.matchconditions, create a new ProjectionSpec
        object and add this item to self.projections.
        """
        sheetspec_product = itertools.product(model.sheets.path_items.values(),
                                              model.sheets.path_items.values())
        for src_sheet, dest_sheet in sheetspec_product:

            conditions = self.definition.compute_conditions(dest_sheet.level, self,
                                                            dest_sheet.properties)
            for matchname, matchconditions in conditions.items():

                if self._matchcondition_holds(matchconditions, src_sheet):

                    paramfn = self.definition.lookup(self.__class__, matchname, 'method')
                    projtype = self.definition.lookup(self.__class__, matchname, 'type')

                    proj = ProjectionSpec(projtype, src_sheet, dest_sheet)

                    paramsets = paramfn(self, src_sheet.properties, dest_sheet.properties)
                    paramsets = [paramsets] if isinstance(paramsets, dict) else paramsets

                    for paramset in paramsets:
                        proj = ProjectionSpec(projtype, src_sheet, dest_sheet)
                        proj.update(**paramset)
                        # Only used when time_dependent=False
                        # (which is to be deprecated)
                        proj.matchname = matchname

                        path = (str(dest_sheet), paramset['name'])
                        model.projections.set_path(path, proj)
        return model


    def __call__(self,setup_options=True, instantiate_options=True, verbose=False):
        """
        A convenient way to setup a model object, instantiate it and
        return it.
        """
        model = self.setup(setup_options)
        model(instantiate_options, verbose)
        return model


    def __getitem__(self, key):
        "Convenient property access."
        return self.properties[key]


    def __setitem__(self, key, val):
        raise NotImplementedError("Models must define properties via the property_setup method")


    def keys(self):
        "The list of available property keys."
        return self.properties.keys()


    def items(self):
        "The property items."
        return self.properties.items()



# Register the sheets and projections available in Topographica
from topo.sheet import optimized as sheetopt
from topo.projection import optimized as projopt
from topo import projection

sheet_classes = [c for c in topo.sheet.__dict__.values() if
                 (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

sheet_classes_opt = [c for c in sheetopt.__dict__.values() if
                     (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

projection_classes = [c for c in projection.__dict__.values() if
                      (isinstance(c, type) and issubclass(c, projection.Projection))]

projection_classes_opt = [c for c in projopt.__dict__.values() if
                          (isinstance(c, type) and issubclass(c, projection.Projection))]

for obj_class in (sheet_classes + sheet_classes_opt
                  + projection_classes + projection_classes_opt):
    with param.logging_level('CRITICAL'):
        # Do not create a decorator if declared as abstract
        if not hasattr(obj_class, "_%s__abstract" % obj_class.name):
            Model.register_decorator(obj_class)


