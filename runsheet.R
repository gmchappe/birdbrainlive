#sourcing all programs
source("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\bbsetup.R")
source("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\bbinput.R")
source("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\bbpoints.R")
source("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\bbsham.R")
source("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\bbround_udisc.R")
source("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\bbpools.R")

# FILL IN THE FOLLOWING INPUTS:
# create output folder and set it as working directory. This will be where the round recaps go.
setwd("C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\output")

# input <- folder where results/exports are saved (just makes the input for in a little less clunky)
input <- "C:\\Users\\gmcha\\OneDrive\\Desktop\\birdbrain26\\input\\"

# output <- GoogleSheet where the league outputs will go
out <- 'https://docs.google.com/spreadsheets/d/1_NvpAOZSjCd-hvwM_3MCx6DKh8aHnvdPXY-3zuKeaV0/edit?gid=0'

# league setup
# only to be done once at the start of the season!
# see README for options if you've run this in a previous season.
# bbsetup(link = out,
#         first = 0,
#         oldwb = 'https://docs.google.com/spreadsheets/d/1Kbaa2c3rCCoG0eShL1ktklP-MLSliiDWCfnILd3tYn4/edit?gid=1858177861',
#         year = 2025)
# 
# Now fill in the schedule on the generated GoogleSheet.
library(googlesheets4)
gs4_auth()
2
# league rounds (fill in 1-X, and then the links to the individual round spreadsheets as they are created/downloaded to the input folder)
# bbround_udisc(1,paste0(input,'birdbrain-disc-golf-club-birdbrain-season-opener-shady-oaks-2026-04-12'),out)
# bbround_udisc(2,paste0(input, 'birdbrain-disc-golf-club-birdbrain-black-bear-2026-04-16'),out)
# bbround_udisc(3,paste0(input, 'birdbrain-disc-golf-club-birdbrain-margreth-riemer-2026-04-19'),out)
# bbround_udisc(4,paste0(input, 'birdbrain-disc-golf-club-birdbrain-sunrise-park-2026-04-23'),out)
# bbround_udisc(5,paste0(input, 'birdbrain-disc-golf-club-birdbrain-kress-creek-2026-04-26'),out)
# bbround_udisc(6,paste0(input, 'birdbrain-disc-golf-club-birdbrain-margreth-riemer-palatine-double-points-2026-04-30'),out)
# bbround_udisc(7,paste0(input, 'birdbrain-disc-golf-club-birdbrain-highland-pilcher-park-2026-05-03'),out)
# bbround_udisc(8,paste0(input, 'birdbrain-disc-golf-club-birdbrain-fel-pro-rrr-2026-05-07'),out)
# bbround_udisc(9,paste0(input, 'birdbrain-disc-golf-club-birdbrain-black-bear-ctp-palooza-2026-05-09'),out)
# bbround_udisc(10,paste0(input,'birdbrain-disc-golf-club-birdbrain-shady-oaks-double-points-2026-05-14'),out)
# bbround_udisc(11,paste0(input,'birdbrain-disc-golf-club-birdbrain-indian-oaks-2026-05-17'),out)
# bbround_udisc(12,paste0(input,'birdbrain-disc-golf-club-birdbrain-rolling-knolls-2026-05-21'),out)
# bbround_udisc(13,paste0(input,'birdbrain-disc-golf-club-birdbrain-walnut-hollow-double-points-2026-05-24'),out)
# bbround_udisc(14,paste0(input,'birdbrain-disc-golf-club-birdbrain-margreth-riemer-reservoir-2026-05-28'),out)
# bbround_udisc(15,paste0(input,'birdbrain-disc-golf-club-birdbrain-fel-pro-rrr-2026-05-31'),out)
# bbround_udisc(16,paste0(input,'birdbrain-disc-golf-club-birdbrain-kress-creek-2026-06-04'),out)
# bbround_udisc(17,paste0(input,'birdbrain-disc-golf-club-birdbrain-fairfield-gold-2026-06-07'),out)
# #bbround_udisc(18,paste0(input,''),out) cancelled (rain)
# bbround_udisc(19,paste0(input,'birdbrain-disc-golf-club-birdbrain-shady-oaks-2026-06-14'),out)
# bbround_udisc(20,paste0(input,'birdbrain-disc-golf-club-birdbrain-fel-pro-rrr-5-sol-round-5-speed-or-lower-2026-06-18'),out)
# bbround_udisc(21,paste0(input, 'birdbrain-disc-golf-club-birdbrain-black-bear-saturday-round-2026-06-20'),out)
# bbround_udisc(22,paste0(input, 'birdbrain-disc-golf-club-birdbrain-margreth-riemer-reservoir-palatine-2026-06-25'),out)
# bbround_udisc(23,paste0(input, 'birdbrain-disc-golf-club-birdbrain-walnut-hollow-2026-06-28'),out)
# bbround_udisc(24,paste0(input, 'birdbrain-disc-golf-club-birdbrain-sunrise-park-2026-07-02'),out)
# bbround_udisc(25,paste0(input, 'birdbrain-disc-golf-club-birdbrain-rolling-knolls-double-points-2026-07-05'),out)
# bbround_udisc(26,paste0(input, 'birdbrain-disc-golf-club-birdbrain-walnut-dgc-schaumburg-2026-07-09'),out)
# bbround_udisc(27,paste0(input, 'birdbrain-disc-golf-club-birdbrain-indian-oaks-2026-07-12'),out)
# bbround_udisc(28,paste0(input, 'birdbrain-disc-golf-club-birdbrain-fel-pro-2026-07-16'),out)
# bbround_udisc(29,paste0(input, 'birdbrain-disc-golf-club-birdbrain-knoch-knolls-2026-07-19'),out)
# bbround_udisc(30,paste0(input, 'birdbrain-disc-golf-club-birdbrain-sunrise-park-2026-07-23'),out)
# bbround_udisc(31,paste0(input, 'birdbrain-disc-golf-club-birdbrain-fairfield-park-2026-07-26'),out)
# bbround_udisc(32,paste0(input, 'birdbrain-disc-golf-club-birdbrain-rolling-knolls-2026-07-30'),out)
# bbround_udisc(33,paste0(input, 'birdbrain-disc-golf-club-birdbrain-kress-creek-the-tips-2026-08-02'),out)
# bbround_udisc(34,paste0(input, 'birdbrain-disc-golf-club-birdbrain-shady-oaks-double-points-2026-08-06'),out)
# #bbround_udisc(35,paste0(input, ''),out) cancelled (rain)
# bbround_udisc(36,paste0(input, 'birdbrain-disc-golf-club-birdbrain-margreth-riemer-palatine-2026-08-13'),out)
# bbround_udisc(37,paste0(input, 'birdbrain-disc-golf-club-birdbrain-indian-oaks-2026-08-16'),out)
#bbround_udisc(38,paste0(input, 'birdbrain-disc-golf-club-birdbrain-walnut-dgc-schaumburg-2026-08-20'),out)
#bbround_udisc(39,paste0(input, 'birdbrain-disc-golf-club-birdbrain-rolling-knolls-2026-08-27'),out)
bbround_udisc(40,paste0(input, 'birdbrain-disc-golf-club-birdbrain-fairfield-gold-double-points-2026-08-30'),out)
#bbround_udisc(41,paste0(input, ''),out)
#bbround_udisc(42,paste0(input, ''),out)
#bbround_udisc(43,paste0(input, ''),out)



# at any point between rounds, you can run bbpools() to re-adjust the pool assignments and slope/rating of your league.
# this will automatically occur for the first time at round 11.
# i'd recommend every 5-15 rounds, depending on the number of unique league members you have.
bbpools(link = out)