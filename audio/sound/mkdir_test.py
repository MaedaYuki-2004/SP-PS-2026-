import os

# 作成したいフォルダの数
num_folder = 30
 
for i in range(num_folder):
    make = './word%d' % (i+1)
    os.mkdir(make)