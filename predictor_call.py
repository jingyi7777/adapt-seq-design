"""Call the predictor module to make predictions on data.

This is meant for data outside the training/test data.
"""

import argparse
import os

# Disable some (but apparently not all) INFO and WARNING messages
# from TensorFlow; must come before the TensorFlow import
os.environ['TF_CPP_MIN_LOG_LEVEL'] = "2"

import numpy as np
import tensorflow as tf

__author__ = 'Hayden Metsky <hayden@mit.edu>'


# Define function for creating a one-hot encoding from
# nucleotide sequence
FASTA_CODES = {'A': set(('A')),
               'T': set(('T')),
               'C': set(('C')),
               'G': set(('G')),
               'K': set(('G', 'T')),
               'M': set(('A', 'C')),
               'R': set(('A', 'G')),
               'Y': set(('C', 'T')),
               'S': set(('C', 'G')),
               'W': set(('A', 'T')),
               'B': set(('C', 'G', 'T')),
               'V': set(('A', 'C', 'G')),
               'H': set(('A', 'C', 'T')),
               'D': set(('A', 'G', 'T')),
               'N': set(('A', 'T', 'C', 'G'))}
onehot_idx = {'A': 0, 'C': 1, 'G': 2, 'T': 3}
def onehot(b):
    # One-hot encoding of base b
    real_bases = FASTA_CODES[b]
    v = [0, 0, 0, 0]
    for b_real in real_bases:
        assert b_real in onehot_idx.keys()
        v[onehot_idx[b_real]] = 1.0 / len(real_bases)
    return v


# Predictor is from ADAPT repository.
class Predictor:
    """This calls the activity models and memoizes results.
    """

    def __init__(self, classification_model_path, regression_model_path,
            classification_threshold=None,
            regression_threshold=None):
        """
        Args:
            classification_model_path: path to serialized classification model
            regression_model_path: path to serialized regression model
            classification_threshold: call guide-target active when
                classifier prediction is >= this threshold (in [0,1]); if
                None, use default threshold with model
            regression_threshold: call guide-target highly active
                when regression prediction (on active data points) is
                >= this threshold (in [0,4]); if None, use default
                threshold with model
        """
        # Load classification and regression models
        self.classification_model = tf.keras.models.load_model(
                classification_model_path)
        self.regression_model = tf.keras.models.load_model(
                regression_model_path)

        # Activity yalues are >= -4 (and most, but not all, are < 0); likewise,
        # outputs of the regression model should be >= -4 (though some
        # may be < -4, i.e., less than any training data value)
        # Shift up regression predictions to be >= 0
        self.regression_lower_bound = 0.0
        self.regression_shift = 4.0

        # Load context_nt; this should be the same for the classification
        # and regression models
        classification_context_nt_path = os.path.join(
                classification_model_path, 'assets.extra/context_nt.arg')
        regression_context_nt_path = os.path.join(
                regression_model_path, 'assets.extra/context_nt.arg')
        if not os.path.isfile(classification_context_nt_path):
            raise Exception(("Unknown context_nt for classification model; "
                "the model should have a assets.extra/context_nt.arg file"))
        if not os.path.isfile(regression_context_nt_path):
            raise Exception(("Unknown context_nt for regression model; "
                "the model should have a assets.extra/context_nt.arg file"))
        with open(classification_context_nt_path) as f:
            classification_context_nt = int(f.readline().strip())
        with open(regression_context_nt_path) as f:
            regression_context_nt = int(f.readline().strip())
        if classification_context_nt != regression_context_nt:
            raise Exception(("Classification and regression models should "
                "have been trained with the same context_nt, but they differ"))
        self.context_nt = classification_context_nt

        # Read classification and regression thresholds
        # The classification threshold decides which guide-target pairs are
        # active, and the regression threshold (trained only on active pairs)
        # decides which are highly active
        if classification_threshold is None:
            # Read default threshold
            classification_default_threshold_path = os.path.join(
                    classification_model_path,
                    'assets.extra/default_threshold.arg')
            if not os.path.isfile(classification_default_threshold_path):
                raise Exception(("Unknown default threshold for classification "
                    "model; the model should have a "
                    "assets.extra/default_threshold.arg file"))
            with open(classification_default_threshold_path) as f:
                classification_threshold = float(f.readline().strip())
            assert 0 <= classification_threshold <= 1
        else:
            if classification_threshold < 0 or classification_threshold > 1:
                raise ValueError(("Classification threshold should be "
                    "in [0,1]"))
        self.classification_threshold = classification_threshold
        if regression_threshold is None:
            # Read default threshold
            regression_default_threshold_path = os.path.join(
                    regression_model_path,
                    'assets.extra/default_threshold.arg')
            if not os.path.isfile(regression_default_threshold_path):
                raise Exception(("Unknown default threshold for regression "
                    "model; the model should have a "
                    "assets.extra/default_threshold.arg file"))
            with open(regression_default_threshold_path) as f:
                regression_threshold = float(f.readline().strip())
            # Add to the regression threshold the shift, as the default
            # threshold (in contrast to a specified threshold) is in
            # the range of the direct model outputs
            regression_threshold += self.regression_shift
            assert regression_threshold > self.regression_lower_bound
        else:
            if regression_threshold < 0:
                raise ValueError(("Regression threshold should be >= 0"))
        self.regression_threshold = regression_threshold

        # Memoize evaluations, organized by guide start position:
        #   {guide start: {pair: X}}
        # where each X is a tuple of (overall activity, False/True indicating
        # whether highly active)
        self._memoized_evaluations = {}

    def _model_input_from_nt(self, pairs):
        """Create one-hot input to models from nucleotide sequence.

        Args:
            pairs: list of tuples (target with context, guide)

        Returns:
            list of one-hot encodings for each pair
        """
        if len(pairs) == 0:
            return []

        l = 2*self.context_nt + len(pairs[0][1])
        x = np.empty((len(pairs), l, 8), dtype='f')
        for i, (target_with_context, guide) in enumerate(pairs):
            assert len(target_with_context) == 2*self.context_nt + len(guide)

            # Determine one-hot encodings -- i.e., an input vector
            input_vec = []
            for pos in range(self.context_nt):
                v_target = onehot(target_with_context[pos])
                v_guide = [0, 0, 0, 0]
                input_vec += [v_target + v_guide]
            for pos in range(len(guide)):
                v_target = onehot(target_with_context[self.context_nt + pos])
                v_guide = onehot(guide[pos])
                input_vec += [v_target + v_guide]
            for pos in range(self.context_nt):
                v_target = onehot(target_with_context[self.context_nt + len(guide) + pos])
                v_guide = [0, 0, 0, 0]
                input_vec += [v_target + v_guide]
            input_vec = np.array(input_vec, dtype='f')
            x[i] = input_vec
        return x

    def _predict_from_onehot(self, model, pairs_onehot):
        """Predict activity, from one-hot encoded nucleotide sequence,
        using a model.

        Args:
            model: model object (serialized SavedModel) that has a
                call() function
            pairs_onehot: list of one-hot encoded pairs of target and guide

        Returns:
            list of outputs (float) directly from model, with one
            value per item in pairs
        """
        pred_activity = model.call(pairs_onehot, training=False)
        pred_activity = [p[0] for p in pred_activity.numpy()]
        return pred_activity

    def _classify_and_decide(self, pairs_onehot):
        """Run classification model and decide activity.

        Args:
            pairs_onehot: list of one-hot encoded pairs of target and guide

        Returns:
            list of False or True giving result of whether each pair is active
            based on classification, after making decision based on
            self.classification_threshold
        """
        if len(pairs_onehot) == 0:
            return []
        pred_classification_score = self._predict_from_onehot(
                self.classification_model, pairs_onehot)
        return [bool(p >= self.classification_threshold)
                for p in pred_classification_score]

    def _regress(self, pairs_onehot):
        """Run regression model.

        Args:
            pairs_onehot: list of one-hot encoded pairs of target and guide

        Returns:
            list of regression results
        """
        if len(pairs_onehot) == 0:
            return []
        pred_regression_score = self._predict_from_onehot(
                self.regression_model, pairs_onehot)

        # The regression model is trained (and outputs) negative values;
        # shift these up to be positive and enforce a lower-bound
        pred_regression_score = [max(self.regression_lower_bound,
            p + self.regression_shift) for p in pred_regression_score]

        return pred_regression_score

    def _combine_model_results(self, classification_results,
            regression_results):
        """Combine classification and regression results to calculate
        a single activity for each pair and determine whether each pair
        is highly acitve.

        The single activity is 0 if the point is classified as inactive,
        and is the regression output if classified as active. Note that
        the activity value can be 0 even if classified as active, if the
        regression value is <= 0.

        A pair is determined to be highly active if it is *both* classified
        as active and its regression output is above a threshold.

        Args:
            classification_results: output of classification decisions
                for each pair (list of False or True, indicating
                whether active)
            regression_results: output of regression model, only on
                pairs decided to be active from classification

        Returns:
            list of tuples (a, h) where a is a single activity score for
            a pair and h is False or True to indicate whether a pair is
            both active and highly active
        """
        num_pairs = len(classification_results)

        # Merge the results of classification and regression
        results = [(None, None) for _ in range(len(classification_results))]
        j = 0
        for i in range(num_pairs):
            if classification_results[i] is False:
                # Classified inactive
                # The activity is 0, and it is not highly active
                results[i] = (0, False)
            else:
                # Classified active, so use regression results
                # that decide if highly active
                activity = regression_results[j]
                highly_active = bool(regression_results[j] >=
                        self.regression_threshold)
                results[i] = (activity, highly_active)
                j += 1
        assert j == len(regression_results)

        return results

    def _run_models_and_memoize(self, start_pos, pairs):
        """Run classification model and regression model, and memoize
        all results.

        This runs classification on all pairs, and then regression
        on just the ones decided to be active.

        It memoizes results returned by _combine_model_results().

        Args:
            start_pos: start position of all guides in pairs
            pairs: list of tuples (target with context, guide)
        """
        # Create one-hot encoding of pairs
        pairs_onehot = self._model_input_from_nt(pairs)

        # Run classification on all pairs
        classification_results = self._classify_and_decide(pairs_onehot)

        # Pull out pairs that are active
        pairs_onehot_active = [pair_onehot
                for pair_onehot, p in zip(pairs_onehot, classification_results)
                if p is True]

        # Run regression on the active pairs
        regression_results = self._regress(pairs_onehot_active)

        # Compute results
        combined_results = self._combine_model_results(classification_results,
                regression_results)

        # Memoize results
        if start_pos not in self._memoized_evaluations:
            self._memoized_evaluations[start_pos] = {}
        mem = self._memoized_evaluations[start_pos]
        for pair, r in zip(pairs, combined_results):
            mem[pair] = r

    def determine_highly_active(self, start_pos, pairs):
        """Evaluate whether guide-target pairs are highly active.

        Args:
            start_pos: start position of all guides in pairs; used for
                memoizations
            pairs: list of tuples (target with context, guide)

        Returns:
            list of False or True indicating whether each pair is highly active
        """
        # Determine which pairs do not have memoized results, and call
        # these
        if start_pos not in self._memoized_evaluations:
            self._memoized_evaluations[start_pos] = {}
        mem = self._memoized_evaluations[start_pos]
        unique_pairs_to_evaluate = [pair for pair in set(pairs)
                if pair not in mem]
        self._run_models_and_memoize(start_pos, unique_pairs_to_evaluate)

        mem = self._memoized_evaluations[start_pos]
        return [mem[pair][1] for pair in pairs]

    def compute_activity(self, start_pos, pairs):
        """Compute a single activity measurement for pairs.

        Args:
            start_pos: start position of all guides in pairs; used for
                memoizations
            pairs: list of tuples (target with context, guide)

        Returns:
            activity value for each pair
        """
        # Determine which pairs do not have memoized results, and call
        # these
        if start_pos not in self._memoized_evaluations:
            self._memoized_evaluations[start_pos] = {}
        mem = self._memoized_evaluations[start_pos]
        unique_pairs_to_evaluate = [pair for pair in set(pairs)
                if pair not in mem]
        self._run_models_and_memoize(start_pos, unique_pairs_to_evaluate)

        return [mem[pair][0] for pair in pairs]

    def cleanup_memoized(self, start_pos):
        """Cleanup memoizations no longer needed at a start position.

        Args:
            start_pos: start position of all guides to remove
        """
        if start_pos in self._memoized_evaluations:
            del self._memoized_evaluations[start_pos]


def read_inputs(fn):
    """Read guides and targets to test.

    Args:
        fn: path to TSV file; col 1 is target with context, col 2 is
            guide

    Returns:
         list of tuples (target with context, guide)
    """
    inputs = []
    with open(fn) as f:
        for line in f:
            line = line.rstrip()
            ls = line.split('\t')
            assert len(ls) == 2
            target_with_context, guide = ls

            if len(target_with_context) < len(guide):
                raise Exception(("Target with context is shorter than "
                    "guide in input"))

            inputs += [(target_with_context, guide)]
    return inputs


def write_preds(inputs, preds, fn):
    """Write input together with predictions.

    Args:
        inputs: output of read_inputs()
        preds: list of predicted values, one for each input
        fn: path to output TSV file
    """
    with open(fn, 'w') as fw:
        for (target_with_context, guide), pred in zip(inputs, preds):
            row = [target_with_context, guide, pred]
            fw.write('\t'.join(str(x) for x in row) + '\n')


def main(args):
    predictor = Predictor(args.classification_model_path,
            args.regression_model_path)
    inputs = read_inputs(args.input_seqs)

    # Check context_nt matches input data
    for target_with_context, guide in inputs:
        expected_target_len = len(guide) + 2*predictor.context_nt
        if expected_target_len != len(target_with_context):
            raise Exception(("Amount of context in target does not match "
                "expected amount from model (%d)") % model.context_nt)

    # Make predictions
    # Start position is irrelevant since we do not care about
    # memoizations
    preds = predictor.compute_activity(-1, inputs)
    write_preds(inputs, preds, args.output)


if __name__ == "__main__":
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('classification_model_path',
            help=("Path to classification model in TensorFlow's SavedModel "
                  "format"))
    parser.add_argument('regression_model_path',
            help=("Path to regression model in TensorFlow's SavedModel "
                  "format"))
    parser.add_argument('input_seqs',
            help=("Path to TSV file containing input sequences. Each row "
                  "corresponds to a guide-target pair to test. First col "
                  "gives the target with context and second col gives the "
                  "guide (guide should match the center)"))
    parser.add_argument('output',
            help=("Path to output TSV; format is the input TSV, with an "
                  "added column giving predicted value"))

    args = parser.parse_args()

    main(args)
