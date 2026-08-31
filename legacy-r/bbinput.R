# Birdbrain 2026 function script
# C GeoffStats LLC 2026
# function: bbinput()
# input: 
  # wb - the google sheets link (look at bbsetup())
  # round number (within the schedule file if unsure!)
# output: list of all necessary input dfs for birdbrain round processing

# Current Input Index
# 1. Entire Season Schedule
# 2. Schedule Information for Today's Round
# 3. Standings Prior to Update (tags removed)
# 4. Pools (for round result - using previous season's classifications!)
# 5. Pool Averages by Layout (needed for WCS slope calculation)
# 6. Handicap Adjustments
# 7. All Course Records (in case it is broken and the sheet will need to be appended)
# 8. Course Record Check (will see if new layout or existing record, then check what is there against hot round of today)
# 9. All Time Through [two seasons ago] by Season
# 10. Current All-Time including previous Season
# 11. Aces (in case one is hit and sheet will need to be appended)
# 12. FULL schedule (to put ace pot back into schedule).
# 13. Full Season Scores (for pool assigning and sample size determination to move on to slope calculations)

# requires:
# dplyr (pipes)
# googlesheets4 (access to Google Sheet)

bbinput <- function(wb,round)
{
  require(dplyr)
  require(googlesheets4)
  
  cat(paste0("Loading League Data...\n\n"))
  
  schedule <- read_sheet(ss=wb,
                         sheet = "League Schedule") %>%
    filter(!is.na(RoundNo))
  todaysched <- as.data.frame(schedule) %>%
    mutate(multiplier = case_when(is.na(Note) ~ 1,
                                  Note == "DOUBLE POINTS ($10)" | Note == "DOUBLE POINTS ($25 for tag)" ~ 2,
                                  TRUE ~ 1)) %>%
    filter(RoundNo == round)
  
  nexttime <- as.data.frame(schedule) %>%
    filter(RoundNo == round + 1)
  
  leaderboard <- read_sheet(ss=wb,
                            sheet = 'Leaderboard') %>%
    rename(name=Name)
  
  pools <- read_sheet(ss=wb,
                      sheet = 'Player Pool Assignments')
  
  slope <- read_sheet(ss=wb,
                      sheet = 'Poolwise Strokes by Round')
  
  oldhcp <- read_sheet(ss=wb,
                       sheet = 'Handicap')
  
  oldrec <- read_sheet(ss=wb,
                       sheet = 'Course Records')
  
  reccheck <- oldrec %>%
    filter(Course == todaysched$Course & Layout == todaysched$Layout)
  
  alltime <- read_sheet(ss=wb,
                        sheet = "Past All Time")
  
  newat <- read_sheet(ss=wb,
                      sheet = 'Current All Time')
  
  oldace <- read_sheet(ss=wb,
                       sheet = 'Aces')
  
  allscores <- read_sheet(ss=wb,
                          sheet='Full Season Scores')
  
  bbinput <- list(todaysched,nexttime,leaderboard,pools,slope,oldhcp,oldrec,reccheck,alltime,newat,oldace,schedule,allscores)
  
  cat(paste0("Done.\n\n"))
  
  return(bbinput)
}

# function: sink.reset()
# input: null
# output: null
# resets sink() to prevent overwriting previous round's TXT file.
# requires: null
sink.reset <- function(){
  for(i in seq_len(sink.number())){
    sink(NULL)
  }
}