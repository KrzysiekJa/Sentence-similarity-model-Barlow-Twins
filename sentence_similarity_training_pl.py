import os
import gzip
import csv
import math
import time
import numpy as np
from datetime import datetime

import torch
from torch import nn, Tensor
from torch.utils.data import DataLoader
from datasets import load_dataset # for pl sets

from sentence_transformers import util, InputExample
from sentence_transformers.evaluation import SimilarityFunction, EmbeddingSimilarityEvaluator

try:
    from torch.utils.tensorboard import SummaryWriter
except ImportError:
    os.system('pip install tensorboardX')
    from tensorboardX import SummaryWriter

from sentence_transformer import SentenceTransformer
from evaluators import LossEvaluator
from losses import BarlowTwinsLoss
from utility_functions import *



def main( run, language: str ):
    ########################################################################
    # Checking if dataset exsist. If not, needed to download and extract
    ########################################################################
    dataset_names = {'main': 'cdsc', 'relatedness': 'cdsc-r'}
    ########################################################################
    # Training parameters
    ########################################################################
    model_name = os.environ.get("MODEL_NAME") # 'allegro/herbert-base-cased'
    lambda_    = float( os.environ.get("LAMBDA_") )
    batch_size = 32
    num_epochs = 4
    model_save_path = 'output/fine_tuning_benchmark-'+model_name.replace('/', '_')+'-'+datetime.now().strftime("%Y-%m-%d_%H-%M")
    params = {
        "optimizer": {
            "type": "AdamW",
            "lr": 2e-5,
            "eps": 1e-12,
        }, 
    }
    run = set_neptun_params(run, 
        {
            "model_name": model_name,
            "params": params,
            "lambda": lambda_,
            "batch_size": batch_size,
            "num_epochs": num_epochs,
            "dataset_name": dataset_names['relatedness'],
            "language": language
        }
    )
    ########################################################################
    # Loading a pre-trained sentence transformer model
    ########################################################################
    model = SentenceTransformer(model_name)
    ########################################################################
    # Extra functions
    ########################################################################
    def prepare_samples(reader):
        samples = []

        for row in reader:
            score = float(row['relatedness_score']) / 5.0  # Normalize score to range 0 ... 1
            inp_example = InputExample(texts=[row['sentence_A'], row['sentence_B']], label=score)
            samples.append(inp_example)
    
        return samples
    ########################################################################
    # Loading and preparing data
    ########################################################################
    train_dataset = load_dataset(dataset_names['main'], dataset_names['relatedness'], split='train')
    dev_dataset   = load_dataset(dataset_names['main'], dataset_names['relatedness'], split='validation')
    
    train_samples = prepare_samples(train_dataset)
    test_samples  = prepare_samples(dev_dataset)
    dev_samples   = train_samples[: len(train_samples)//10] # first 10%
    train_samples = train_samples[len(train_samples)//10 :] # last 90%
    
    ########################################################################
    # Configuring training parameters and process objects
    ########################################################################
    train_dataloader = DataLoader(train_samples, shuffle=True, batch_size=batch_size)
    log_dir = 'output/logs'
    train_loss = BarlowTwinsLoss(model=model, lambda_=lambda_)
    dev_evaluator = LossEvaluator(dev_samples, loss_model=train_loss, log_dir=log_dir, show_progress_bar=True, batch_size=batch_size)
    
    def neptune_callback(score, epoch, steps):
        global run
        run[f"epochs_val/val_loss"].append(score)
    
    evaluation_steps = len(train_dataloader) // 10
    warmup_steps = math.ceil( len(train_dataloader) * num_epochs * 0.1 )
    
    run = set_neptun_train_params(run,
        {
            "train_steps": len(train_dataloader),
            "evaluation_steps": evaluation_steps,
            "warmup_steps": warmup_steps
        }
    )
    ########################################################################
    # Model training
    ########################################################################
    start = time.perf_counter()

    model.fit(
              train_objectives=[(train_dataloader, train_loss)],
              evaluator=dev_evaluator,
              epochs=num_epochs,
              evaluation_steps=evaluation_steps,
              show_progress_bar=True,
              warmup_steps=warmup_steps,
              optimizer_params={'lr': params['optimizer']['lr'], 'eps': params['optimizer']['eps']},
              callback=neptune_callback,
              output_path=model_save_path,
              training_samples=train_samples,
              run=run
    )
    end = time.perf_counter()
    
    run = set_neptun_time_perf(run, end, start)
    ########################################################################
    # Testing process
    ########################################################################
    model = SentenceTransformer(model_save_path)
    
    test_evaluator = EmbeddingSimilarityEvaluator.from_input_examples(
                test_samples, 
                main_similarity=SimilarityFunction.COSINE
    )
    test_evaluation = test_evaluator(model, output_path=model_save_path)
    
    run["test/test_accuracy"].append(test_evaluation)
    neptun_final_steps(run, language, model_save_path)



if __name__ =='__main__':
    seed = 12 # on basis of: https://arxiv.org/pdf/2002.06305.pdf
    language = 'pl'
    tags = ["colab", "tests", "similarity", language]
    name = "basic-colab-example"
    set_seeds( seed )
    run = init_learning_env( name, tags ) # returned: neptune.Run object
    main( run, language )



