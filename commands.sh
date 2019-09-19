# Commands used to generate data & models.

###########################################################
# Cas13 data
# exp-and-pos subset, regression only on active data points

# Perform hyperparameter search for the best model
python -u predictor_hyperparam_search.py --dataset cas13 --cas13-subset exp-and-pos --cas13-regress-only-on-active --context-nt 10 --command hyperparam-search --hyperparam-search-cross-val-num-splits 5 --search-type random --num-random-samples 500 --params-mean-val-loss-out-tsv out/cas13-hyperparam-search.exp-and-pos.regress-on-active.tsv --save-models models/predictor_exp-and-pos_regress-on-active --seed 1 &> out/cas13-hyperparam-search.exp-and-pos.regress-on-active.out

# Select model 524b9795 from the above, which has the lowest
# MSE with SEM <0.1 for both MSE and 1_minus_rho

# Create test results
python predictor.py --load-model models/predictor_exp-and-pos_regress-on-active/model-524b9795 --dataset cas13 --cas13-subset exp-and-pos --cas13-regress-only-on-active --context-nt 10 --write-test-tsv out/cas13-hyperparam-search.exp-and-pos.regress-on-active.model-524b9795.test.tsv.gz --seed 1 &> out/cas13-hyperparam-search.exp-and-pos.regress-on-active.model-524b9795.test.out

# Plot test results
Rscript plotting_scripts/plot_predictor_test_results.R out/cas13-hyperparam-search.exp-and-pos.regress-on-active.model-524b9795.test.tsv.gz out/cas13-hyperparam-search.exp-and-pos.regress-on-active.model-524b9795.test.pdf
###########################################################