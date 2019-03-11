import numpy as np
from tqdm import tqdm


from ..evaluation import rank_score, mrr_score, hits_at_n_score, mar_score
import os
from joblib import Parallel, delayed
import itertools
import tensorflow as tf
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def train_test_split_no_unseen(X, test_size=5000, seed=0):
    """Split into train and test sets.

     Test set contains only entities and relations which also occur
     in the training set.

    Parameters
    ----------
    X : ndarray, size[n, 3]
        The dataset to split.
    test_size : int, float
        If int, the number of triples in the test set. If float, the percentage of total triples.
    seed : int
        A random seed used to split the dataset.

    Returns
    -------
    X_train : ndarray, size[n, 3]
        The training set
    X_test : ndarray, size[n, 3]
        The test set

    """
    logger.debug('Creating train test split.')
    if type(test_size) is float:
        logger.debug('Test size is of type float. Converting to int.')
        test_size = int(len(X) * test_size)

    rnd = np.random.RandomState(seed)

    subs, subs_cnt = np.unique(X[:, 0], return_counts=True)
    objs, objs_cnt = np.unique(X[:, 2], return_counts=True)
    rels, rels_cnt = np.unique(X[:, 1], return_counts=True)
    dict_subs = dict(zip(subs, subs_cnt))
    dict_objs = dict(zip(objs, objs_cnt))
    dict_rels = dict(zip(rels, rels_cnt))

    idx_test = []
    logger.debug('Selecting test cases using random search.')
    while len(idx_test) < test_size:
        i = rnd.randint(len(X))
        if dict_subs[X[i, 0]] > 1 and dict_objs[X[i, 2]] > 1 and dict_rels[X[i, 1]] > 1:
            dict_subs[X[i, 0]] -= 1
            dict_objs[X[i, 2]] -= 1
            dict_rels[X[i, 1]] -= 1
            idx_test.append(i)
    logger.debug('Completed random search.')
    idx = np.arange(len(X))
    idx_train = np.setdiff1d(idx, idx_test)
    logger.debug('Train test split completed.')
    return X[idx_train, :], X[idx_test, :]


def _create_unique_mappings(unique_obj,unique_rel):
    obj_count = len(unique_obj)
    rel_count = len(unique_rel)
    rel_to_idx = dict(zip(unique_rel, range(rel_count)))
    obj_to_idx = dict(zip(unique_obj, range(obj_count)))
    return rel_to_idx, obj_to_idx 

def create_mappings(X):
    """Create string-IDs mappings for entities and relations.

        Entities and relations are assigned incremental, unique integer IDs.
        Mappings are preserved in two distinct dictionaries,
        and counters are separated for entities and relations mappings.

    Parameters
    ----------
    X : ndarray, shape [n, 3]
        The triples to extract mappings.

    Returns
    -------
    rel_to_idx : dict
        The relation-to-internal-id associations
    ent_to_idx: dict
        The entity-to-internal-id associations.

    """
    logger.debug('Creating mappings for entities and relations.')
    unique_ent = np.unique(np.concatenate((X[:, 0], X[:, 2])))
    unique_rel = np.unique(X[:, 1])
    return _create_unique_mappings(unique_ent,unique_rel)
 
def create_mappings_entity_with_schema(X, S):
    """Create string-IDs mappings for entities and relations.

        Entities and relations are assigned incremental, unique integer IDs.
        Mappings are preserved in two distinct dictionaries,
        and counters are separated for entities and relations mappings.

    Parameters
    ----------
    X : ndarray, shape [n, 3]
        The triples to extract mappings.

    Returns
    -------
    rel_to_idx : dict
        The relation-to-internal-id associations
    ent_to_idx: dict
        The entity-to-internal-id associations.

    """
    logger.debug('Creating mappings for entities and relations of a schema.')
    unique_ent = np.unique(np.concatenate((X[:, 0], X[:, 2], S[:, 0])))
    unique_rel = np.unique(X[:, 1])
    return _create_unique_mappings(unique_ent,unique_rel)

def create_mappings_schema(S):
    """Create string-IDs mappings for classes and relations of the schema.

        Entities and relations are assigned incremental, unique integer IDs.
        Mappings are preserved in two distinct dictionaries,
        and counters are separated for entities and relations mappings.

    Parameters
    ----------
    X : ndarray, shape [n, 3]
        The triples to extract mappings.

    Returns
    -------
    rel_to_idx : dict
        The relation-to-internal-id associations
    ent_to_idx: dict
        The entity-to-internal-id associations.

    """
    logger.debug('Creating mappings for classes and relations of a schema.')
    unique_class = np.unique(S[:,2])
    unique_rel = np.unique(S[:,1])
    return _create_unique_mappings(unique_class,unique_rel)

def generate_corruptions_for_eval(X, entities_for_corruption, corrupt_side='s+o', table_entity_lookup_left=None, 
                                      table_entity_lookup_right=None, table_reln_lookup=None, rnd=None):
    """Generate corruptions for evaluation.

        Create all possible corruptions (subject and object) for a given triple x, in compliance with the LCWA.

    Parameters
    ----------
    X : Tensor, shape [1, 3]
        Currently, a single positive triples that will be used to create corruptions.
    entities_for_corruption : Tensor
        All the entity IDs which are to be used for generation of corruptions
    corrupt_side: string
        Specifies which side to corrupt the entities. 
        ``s`` is to corrupt only subject.
        ``o`` is to corrupt only object
        ``s+o`` is to corrupt both subject and object
    table_entity_lookup_left : tf.HashTable
        Hash table of subject entities mapped to unique prime numbers
    table_entity_lookup_right : tf.HashTable
        Hash table of object entities mapped to unique prime numbers
    table_reln_lookup : tf.HashTable
        Hash table of relations mapped to unique prime numbers
    rnd: numpy.random.RandomState
        A random number generator.

    Returns
    -------

    out : Tensor, shape [n, 3]
        An array of corruptions for the triples for x.
        
    out_prime : Tensor, shape [n, 3]
        An array of product of prime numbers associated with corruption triples or None 
        based on filtered or non filtered version.

    """
   
    logger.debug('Generating corruptions for evaluation.')

    logger.debug('Getting repeating subjects.')
    if corrupt_side not in ['s+o', 's', 'o']:
        msg = 'Invalid argument value for corruption side passed for evaluation' 
        logger.error(msg)
        raise ValueError(msg)
        
    if corrupt_side in ['s+o', 'o']: #object is corrupted - so we need subjects as it is
        repeated_subjs = tf.keras.backend.repeat(
                                                    tf.slice(X,
                                                        [0, 0], #subj
                                                        [tf.shape(X)[0],1])
                                                , tf.shape(entities_for_corruption)[0])
        repeated_subjs = tf.squeeze(repeated_subjs, 2)
        
    

    logger.debug('Getting repeating object.')
    if corrupt_side in ['s+o', 's']: #subject is corrupted - so we need objects as it is
        repeated_objs = tf.keras.backend.repeat(
                                                    tf.slice(X,
                                                            [0, 2], #Obj
                                                            [tf.shape(X)[0], 1])
                                                , tf.shape(entities_for_corruption)[0])
        repeated_objs = tf.squeeze(repeated_objs, 2)

    logger.debug('Getting repeating relationships.')
    repeated_relns = tf.keras.backend.repeat(
                                                tf.slice(X,
                                                        [0, 1], #reln
                                                        [tf.shape(X)[0], 1])
                                            , tf.shape(entities_for_corruption)[0])
    repeated_relns = tf.squeeze(repeated_relns, 2)
    

    rep_ent = tf.keras.backend.repeat(tf.expand_dims(entities_for_corruption,0), tf.shape(X)[0])
    rep_ent = tf.squeeze(rep_ent, 0)
    
    
    if corrupt_side == 's+o':
        stacked_out = tf.concat([tf.stack([repeated_subjs, repeated_relns, rep_ent], 1),
                        tf.stack([rep_ent, repeated_relns, repeated_objs], 1)],0)

    elif corrupt_side == 'o':
        stacked_out = tf.stack([repeated_subjs, repeated_relns, rep_ent], 1)
        
    else:
        stacked_out = tf.stack([rep_ent, repeated_relns, repeated_objs], 1)
    
    out = tf.reshape(tf.transpose(stacked_out , [0, 2, 1]),(-1,3))
    out_prime = tf.constant([])
    
    logger.debug('Creating prime numbers associated with corruptions.')
    if table_entity_lookup_left!= None and table_entity_lookup_right!=None and table_reln_lookup != None:
        
        if corrupt_side in ['s+o', 'o']:
            prime_subj = tf.squeeze(table_entity_lookup_left.lookup(repeated_subjs))
            prime_ent_right = tf.squeeze(table_entity_lookup_right.lookup(rep_ent))
        
        if corrupt_side in ['s+o', 's']:
            prime_obj = tf.squeeze(table_entity_lookup_right.lookup(repeated_objs))
            prime_ent_left = tf.squeeze(table_entity_lookup_left.lookup(rep_ent))
            
            
        prime_reln =tf.squeeze(table_reln_lookup.lookup(repeated_relns))
        
        if corrupt_side == 's+o':
            out_prime = tf.concat([prime_subj * prime_reln * prime_ent_right, 
                               prime_ent_left * prime_reln * prime_obj],0)

        elif corrupt_side == 'o':
            out_prime = prime_subj * prime_reln * prime_ent_right
        else:
            out_prime = prime_ent_left * prime_reln * prime_obj
            
    logger.debug('Returning corruptions for evaluation.')
    return out, out_prime


def generate_corruptions_for_fit(X, all_entities, eta=1, corrupt_side='s+o', rnd=None):
    """Generate corruptions for training.

        Creates corrupted triples for each statement in an array of statements.

        Strategy as per ::cite:`trouillon2016complex`.

        .. note::
            Collisions are not checked. 
            Too computationally expensive (see ::cite:`trouillon2016complex`).

    Parameters
    ----------
    X : Tensor, shape [n, 3]
        An array of positive triples that will be used to create corruptions.
    all_entities : dict
        The entity-tointernal-IDs mappings
    eta : int
        The number of corruptions per triple that must be generated.
    rnd: numpy.random.RandomState
        A random number generator.

    Returns
    -------

    out : Tensor, shape [n * eta, 3]
        An array of corruptions for a list of positive triples x. For each row in X the corresponding corruption
        indexes can be found at [index+i*n for i in range(eta)]

    """
    logger.debug('Generating corruptions for fit.')
    if corrupt_side not in ['s+o', 's', 'o']:
        msg = 'Invalid argument value {} for corruption side passed for evaluation.'.format(corrupt_side) 
        logger.error(msg)
        raise ValueError(msg)

    dataset =  tf.reshape(tf.tile(tf.reshape(X,[-1]),[eta]),[tf.shape(X)[0]*eta,3])
    
    if corrupt_side == 's+o':
        keep_subj_mask = tf.tile(tf.cast(tf.random_uniform([tf.shape(X)[0]], 0, 2, dtype=tf.int32, seed=rnd),tf.bool),[eta])
    else:
        keep_subj_mask = tf.cast(tf.ones(tf.shape(X)[0]*eta,tf.int32),tf.bool)
        if corrupt_side == 's':
            keep_subj_mask = tf.logical_not(keep_subj_mask)

    keep_obj_mask = tf.logical_not(keep_subj_mask)
    keep_subj_mask = tf.cast(keep_subj_mask,tf.int32)
    keep_obj_mask = tf.cast(keep_obj_mask,tf.int32)
    


    logger.debug('Created corruption masks.')
    replacements = tf.random_uniform([tf.shape(dataset)[0]],0,tf.shape(all_entities)[0], dtype=tf.int32, seed=rnd)

    subjects = tf.math.add(tf.math.multiply(keep_subj_mask,dataset[:,0]),tf.math.multiply(keep_obj_mask,replacements))
    logger.debug('Created corrupted subjects.')
    relationships = dataset[:,1]
    logger.debug('Retained relationships.')
    objects = tf.math.add(tf.math.multiply(keep_obj_mask,dataset[:,2]),tf.math.multiply(keep_subj_mask,replacements))
    logger.debug('Created corrupted objects.')

    out = tf.transpose(tf.stack([subjects,relationships,objects]))

    logger.debug('Returning corruptions for fit.')
    return out           

def _convert_to_idx(X, ent_to_idx, rel_to_idx, obj_to_idx):
    x_idx_s = np.vectorize(ent_to_idx.get)(X[:, 0])
    x_idx_p = np.vectorize(rel_to_idx.get)(X[:, 1])
    x_idx_o = np.vectorize(obj_to_idx.get)(X[:, 2])
    logger.debug('Returning ids.')
    return np.dstack([x_idx_s, x_idx_p, x_idx_o]).reshape((-1, 3))


def to_idx(X, ent_to_idx, rel_to_idx):
    """Convert statements (triples) into integer IDs.

    Parameters
    ----------
    X : ndarray
        The statements to be converted.
    ent_to_idx : dict
        The mappings between entity strings and internal IDs.
    rel_to_idx : dict
        The mappings between relation strings and internal IDs.
    Returns
    -------
    X : ndarray, shape [n, 3]
        The ndarray of converted statements.
    """
    logger.debug('Converting statements to integer ids.')
    if X.ndim==1:
        X = X[np.newaxis,:]
    return _convert_to_idx(X, ent_to_idx, rel_to_idx, ent_to_idx) 


def to_idx_schema(S, ent_to_idx, schema_class_to_idx, schema_rel_to_idx):
    """Convert schema statements (triples) into integer IDs.

    Parameters
    ----------
    X : ndarray
        The statements to be converted.
    ent_to_idx : dict
        The mappings between entity strings and internal IDs.
    rel_to_idx : dict
        The mappings between relation strings and internal IDs.
    Returns
    -------
    X : ndarray, shape [n, 3]
        The ndarray of converted schema statements.
    """

    logger.debug('Converting schema statements to integer ids.')
    return _convert_to_idx(S, ent_to_idx, schema_rel_to_idx, schema_class_to_idx) 


def evaluate_performance(X, model, filter_triples=None, verbose=False, strict=True, rank_against_ent=None, corrupt_side='s+o'):
    """Evaluate the performance of an embedding model.

        Run the relational learning evaluation protocol defined in Bordes TransE paper.

        It computes the mean reciprocal rank, by assessing the ranking of each positive triple against all
        possible negatives created in compliance with the local closed world assumption (LCWA).

    Parameters
    ----------
    X : ndarray, shape [n, 3]
        An array of test triples.
    model : ampligraph.latent_features.EmbeddingModel
        A knowledge graph embedding model
    filter_triples : ndarray of shape [n, 3] or None
        The triples used to filter negatives.
    verbose : bool
        Verbose mode
    strict : bool
        Strict mode. If True then any unseen entity will cause a RuntimeError.
        If False then triples containing unseen entities will be filtered out.
    rank_against_ent: array-like
        List of entities to use for corruptions. If None, will generate corruptions
        using all distinct entities. Default is None.
    corrupt_side: string
        Specifies which side to corrupt the entities. 
        ``s`` is to corrupt only subject.
        ``o`` is to corrupt only object
        ``s+o`` is to corrupt both subject and object
    Returns
    -------
    ranks : ndarray, shape [n]
        An array of ranks of positive test triples.


    Examples
    --------
    >>> import numpy as np
    >>> from ampligraph.datasets import load_wn18
    >>> from ampligraph.latent_features import ComplEx
    >>> from ampligraph.evaluation import evaluate_performance
    >>>
    >>> X = load_wn18()
    >>> model = ComplEx(batches_count=10, seed=0, epochs=1, k=150, eta=10,
    >>>                 loss='pairwise', optimizer='adagrad')
    >>> model.fit(np.concatenate((X['train'], X['valid'])))
    >>>
    >>> filter = np.concatenate((X['train'], X['valid'], X['test']))
    >>> ranks = evaluate_performance(X['test'][:5], model=model, filter_triples=filter)
    >>> ranks
    array([    2,     4,     1,     1, 28550], dtype=int32)
    >>> mrr_score(ranks)
    0.55000700525394053
    >>> hits_at_n_score(ranks, n=10)
    0.8
    """

    logger.debug('Evaluating the performance of the embedding model.')
    X_test = filter_unseen_entities(X, model, verbose=verbose, strict=True)

    X_test = to_idx(X_test, ent_to_idx=model.ent_to_idx, rel_to_idx=model.rel_to_idx)

    ranks = []
    
    
    if filter_triples is not None:
        logger.debug('Getting filtered triples.')
        filter_triples = to_idx(filter_triples, ent_to_idx=model.ent_to_idx, rel_to_idx=model.rel_to_idx)
        model.set_filter_for_eval(filter_triples)
    eval_dict = {}
    
    if rank_against_ent is not None:
        idx_entities = np.asarray([idx for uri, idx in model.ent_to_idx.items() if uri in rank_against_ent])
        eval_dict['corruption_entities']= idx_entities
        
    eval_dict['corrupt_side'] = corrupt_side
    model.configure_evaluation_protocol(eval_dict)
    
    logger.debug('Making predictions.')
    for i in tqdm(range(X_test.shape[0]), disable=(not verbose)):
        y_pred, rank = model.predict(X_test[i], from_idx=True)
        ranks.append(rank)
    model.end_evaluation()
    logger.debug('Returning ranks of positive test triples.')
    return ranks


def filter_unseen_entities(X, model, verbose=False, strict=True):
    """Filter unseen entities in the test set.

    Parameters
    ----------
    X : ndarray, shape [n, 3]
        An array of test triples.
    model : ampligraph.latent_features.EmbeddingModel
        A knowledge graph embedding model
    verbose : bool
        Verbose mode
    strict : bool
        Strict mode. If True then any unseen entity will cause a RuntimeError.
        If False then triples containing unseen entities will be filtered out.

    Returns
    -------
    filtered X : ndarray, shape [n, 3]
        An array of test triples containing no unseen entities.
    """

    logger.debug('Finding entities in test set that are not previously seen by model')
    ent_seen = np.unique(list(model.ent_to_idx.keys()))
    ent_test = np.unique(X[:, [0, 2]].ravel())
    ent_unseen = np.setdiff1d(ent_test, ent_seen, assume_unique=True)

    if ent_unseen.size == 0:
        logger.debug('No unseen entities found.')
        return X
    else:
        logger.debug('Unseen entities found.')
        if strict:
            msg = 'Unseen entities found in test set, please remove or run evaluate_performance() with strict=False.'
            logger.error(msg)
            raise RuntimeError(msg)
        else:
            # Get row-wise mask of triples containing unseen entities
            mask_unseen = np.isin(X, ent_unseen).any(axis=1)

            msg = 'Removing {} triples containing unseen entities. '.format(np.sum(mask_unseen))
            if verbose:
                logger.info(msg)
                print(msg)
            logger.debug(msg)
            return X[~mask_unseen]


def yield_all_permutations(registry, category_type, category_type_params):
    """Yields all the permutation of category type with their respective hyperparams
    
    Parameters
    ----------
    registry: dictionary
        registry of the category type
    category_type: string
        category type values
    category_type_params: list
        category type hyperparams

    Returns
    -------
    name: str
        Specific name of the category
    present_params: list
        Names of hyperparameters of the category
    val: list
        Values of the respective hyperparams
    """
    for name in category_type:
        present_params = []
        present_params_vals = []
        for param in registry[name].external_params:
            try:
                present_params_vals.append(category_type_params[param])
                present_params.append(param)
            except KeyErrori as e:
                logger.debug('Key not found {}'.format(e))
                pass
        for val in itertools.product(*present_params_vals):
            yield name, present_params, val


def gridsearch_next_hyperparam(model_name, in_dict):
    """Performs grid search on hyperparams
    
    Parameters
    ----------
    model_name: string
        name of the embedding model
    in_dict: dictionary 
        dictionary of all the parameters and the list of values to be searched

    Returns:
    out_dict: dict
        Dictionary containing an instance of model hyperparameters.
    """

    from ..latent_features import LOSS_REGISTRY, REGULARIZER_REGISTRY, MODEL_REGISTRY
    logger.debug('Starting gridsearch over hyperparameters. {}'.format(in_dict))
    try:
        verbose = in_dict["verbose"]
    except KeyError:
        logger.debug('Verbose key not found. Setting to False.')
        verbose = False

    try:
        seed = in_dict["seed"]
    except KeyError:
        logger.debug('Seed key not found. Setting to -1.')
        seed = -1 

    try:
        for batch_count in in_dict["batches_count"]:
            for epochs in in_dict["epochs"]:
                for k in in_dict["k"]:
                    for eta in in_dict["eta"]:
                        for reg_type, reg_params, reg_param_values in \
                            yield_all_permutations(REGULARIZER_REGISTRY, in_dict["regularizer"], in_dict["regularizer_params"]):
                            for optimizer_type in in_dict["optimizer"]:
                                for optimizer_lr in in_dict["optimizer_params"]["lr"]:
                                    for loss_type, loss_params, loss_param_values in \
                                        yield_all_permutations(LOSS_REGISTRY, in_dict["loss"], in_dict["loss_params"]):
                                        for model_type, model_params, model_param_values in \
                                            yield_all_permutations(MODEL_REGISTRY, [model_name], in_dict["embedding_model_params"]):
                                            out_dict = {
                                                "batches_count": batch_count,
                                                "epochs": epochs,
                                                "k": k,
                                                "eta": eta,
                                                "loss": loss_type,
                                                "loss_params": {},
                                                "embedding_model_params": {},
                                                "regularizer": reg_type,
                                                "regularizer_params": {},
                                                "optimizer": optimizer_type,
                                                "optimizer_params":{
                                                    "lr": optimizer_lr
                                                    },
                                                "verbose": verbose
                                                }

                                            if seed >= 0:
                                                out_dict["seed"] = seed
                                            #TODO - Revise this, use dict comprehension instead of for loops
                                            for idx in range(len(loss_params)):
                                                out_dict["loss_params"][loss_params[idx]] = loss_param_values[idx]
                                            for idx in range(len(reg_params)):
                                                out_dict["regularizer_params"][reg_params[idx]] = reg_param_values[idx]
                                            for idx in range(len(model_params)):
                                                out_dict["embedding_model_params"][model_params[idx]] = model_param_values[idx] 

                                            yield (out_dict)
    except KeyError as e:
        logger.debug('Hyperparameters are missing from the input dictionary: {}'.format(e))
        print('One or more of the hyperparameters was not passed:')
        print(str(e))


def select_best_model_ranking(model_class, X, param_grid, use_filter=False, early_stopping=False, early_stopping_params={}, use_test_for_selection=True, rank_against_ent=None, corrupt_side='s+o', use_default_protocol=False, verbose=False):
    """Model selection routine for embedding models.

        .. note::
            Model selection done with raw MRR for better runtime performance.

        The function also retrains the best performing model on the concatenation of training and validation sets.

        (note that we generate negatives at runtime according to the strategy described
        in ::cite:`bordes2013translating`).

    Parameters
    ----------
    model_class : class
        The class of the EmbeddingModel to evaluate (TransE, DistMult, ComplEx, etc).
    X : dict
        A dictionary of triples to use in model selection. Must include three keys: `train`, `val`, `test`.
        Values are ndarray of shape [n, 3]..
    param_grid : dict
        A grid of hyperparameters to use in model selection. The routine will train a model for each combination
        of these hyperparameters.
    use_filter : bool
        If True, will use the entire input dataset X to compute filtered MRR
    early_stopping: bool
        Flag to enable early stopping(default:False)
    early_stopping_params: dict
        Dictionary of parameters for early stopping.
        
        The following keys are supported: 
        
            x_valid: ndarray, shape [n, 3] : Validation set to be used for early stopping. Uses X['valid'] by default.
            
            criteria: criteria for early stopping ``hits10``, ``hits3``, ``hits1`` or ``mrr``. (default)
            
            x_filter: ndarray, shape [n, 3] : Filter to be used(no filter by default)
            
            burn_in: Number of epochs to pass before kicking in early stopping(default: 100)
            
            check_interval: Early stopping interval after burn-in(default:10)
            
            stop_interval: Stop if criteria is performing worse over n consecutive checks (default: 3)
    
    use_test_for_selection:bool
        Use test set for model selection. If False, uses validation set. Default(True)        
    rank_against_ent: array-like
        List of entities to use for corruptions. If None, will generate corruptions
        using all distinct entities. Default is None.
    corrupt_side: string
        Specifies which side to corrupt the entities. 
        ``s`` is to corrupt only subject.
        ``o`` is to corrupt only object
        ``s+o`` is to corrupt both subject and object
    use_default_protocol: bool
        Flag to indicate whether to evaluate head and tail corruptions separately(default:False).
        If this is set to true, it will ignore corrupt_side argument and corrupt both head and tail separately and rank triplets.
    verbose : bool
        Verbose mode during evaluation of trained model

    Returns
    -------
    best_model : EmbeddingModel
        The best trained embedding model obtained in model selection.

    best_params : dict
        The hyperparameters of the best embedding model `best_model`.

    best_mrr_train : float
        The MRR (unfiltered) of the best model computed over the validation set in the model selection loop.

    ranks_test : ndarray, shape [n]
        The ranks of each triple in the test set X['test].

    mrr_test : float
        The MRR (filtered) of the best model, retrained on the concatenation of training and validation sets,
        computed over the test set.

    Examples
    --------
    >>> from ampligraph.datasets import load_wn18
    >>> from ampligraph.latent_features import ComplEx
    >>> from ampligraph.evaluation import select_best_model_ranking
    >>>
    >>> X = load_wn18()
    >>> model_class = ComplEx
    >>> param_grid = {
    >>>                     "batches_count": [50],
    >>>                     "seed": 0,
    >>>                     "epochs": [4000],
    >>>                     "k": [100, 200],
    >>>                     "eta": [5,10,15],
    >>>                     "loss": ["pairwise", "nll"],
    >>>                     "loss_params": {
    >>>                         "margin": [2]
    >>>                     },
    >>>                     "embedding_model_params": {
    >>> 
    >>>                     },
    >>>                     "regularizer": ["L2", "None"],
    >>>                     "regularizer_params": {
    >>>                         "lambda": [1e-4, 1e-5]
    >>>                     },
    >>>                     "optimizer": ["adagrad", "adam"],
    >>>                     "optimizer_params":{
    >>>                         "lr": [0.01, 0.001, 0.0001]
    >>>                     },
    >>>                     "verbose": false
    >>>                 }
    >>> select_best_model_ranking(model_class, X, param_grid, use_filter=True, verbose=True, early_stopping=True)

    """
    hyperparams_list_keys = ["batches_count", "epochs", "k", "eta", "loss", "regularizer", "optimizer"]
    hyperparams_dict_keys = ["loss_params", "embedding_model_params",  "regularizer_params",  "optimizer_params"]
    
    for key in hyperparams_list_keys:
        if key not in param_grid.keys() or param_grid[key]==[]:
            logger.debug('Hyperparameter key {} is missing.'.format(key))
            raise ValueError('Please pass values for key {}'.format(key))
            
    for key in hyperparams_dict_keys:
        if key not in param_grid.keys():
            logger.debug('Hyperparameter key {} is missing, replacing with empty dictionary.'.format(key))
            param_grid[key] = {}
    
    #this would be extended later to take multiple params for optimizers(currently only lr supported)
    try:
        lr = param_grid["optimizer_params"]["lr"]
    except KeyError:
        logger.debug('Hypermater key {} is missing'.format(key))
        raise ValueError('Please pass values for optimizer parameter - lr')
    
    model_params_combinations = gridsearch_next_hyperparam(model_class.name, param_grid)
    
    best_mrr_train = 0
    best_model = None
    best_params = None
    
    if early_stopping:
        try:
            early_stopping_params['x_valid']
        except KeyError:
            logger.debug('Early stopping enable but no x_valid parameter set. Setting x_valid to {}'.format(X['valid']))
            early_stopping_params['x_valid'] = X['valid']

    if use_filter:
        X_filter = np.concatenate((X['train'], X['valid'], X['test']))
    else:
        X_filter = None

    if use_test_for_selection:
        selection_dataset = X['test']
    else:
        selection_dataset = X['valid']

        
    for model_params in tqdm(model_params_combinations, disable=(not verbose)):
        model = model_class(**model_params)
        model.fit(X['train'], early_stopping, early_stopping_params)

        if use_default_protocol:
            ranks = evaluate_performance(selection_dataset, model=model, filter_triples=X_filter, verbose=verbose, rank_against_ent=rank_against_ent, corrupt_side='s')
            ranks_obj = evaluate_performance(selection_dataset, model=model, filter_triples=X_filter, verbose=verbose, rank_against_ent=rank_against_ent, corrupt_side='o')
            ranks.extend(ranks_obj)
        else:
            ranks = evaluate_performance(selection_dataset, model=model, filter_triples=X_filter, verbose=verbose, rank_against_ent=rank_against_ent, corrupt_side=corrupt_side)

        curr_mrr = mrr_score(ranks)
        mr = mar_score(ranks)
        hits_1 = hits_at_n_score(ranks, n=1)
        hits_3 = hits_at_n_score(ranks, n=3)
        hits_10 = hits_at_n_score(ranks, n=10)
        info = 'mr:{} mrr: {} hits 1: {} hits 3: {} hits 10: {}, model: {}, params: {}'.format(mr, curr_mrr, hits_1, hits_3, hits_10, type(model).__name__, model_params)
        logger.debug(info)
        if verbose:
            logger.info(info)

        if curr_mrr > best_mrr_train:
            best_mrr_train = curr_mrr
            best_model = model
            best_params = model_params
    
    # Retraining
    best_model.fit(np.concatenate((X['train'], X['valid'])))
    
    if use_default_protocol:
        ranks_test = evaluate_performance(X['test'], model=best_model, filter_triples=X_filter, verbose=verbose, rank_against_ent=rank_against_ent, corrupt_side='s')
        ranks_test_obj = evaluate_performance(X['test'], model=best_model, filter_triples=X_filter, verbose=verbose, rank_against_ent=rank_against_ent, corrupt_side='o')
        ranks_test.extend(ranks_test_obj)
    else:
        ranks_test = evaluate_performance(X['test'], model=best_model, filter_triples=X_filter, verbose=verbose, rank_against_ent=rank_against_ent, corrupt_side=corrupt_side)
    

    mrr_test = mrr_score(ranks_test)

    return best_model, best_params, best_mrr_train, ranks_test, mrr_test

