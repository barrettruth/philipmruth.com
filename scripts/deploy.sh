#!/bin/sh

aws s3 sync ./dist/ s3://philipmruth.com --delete
