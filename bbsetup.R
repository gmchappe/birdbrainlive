# Birdbrain 2026 function script
# C GeoffStats LLC 2026
# function: bbsetup()
# input: 
  # link - GoogleDocs link that will be housing the raw data for the season
  # first season? (binary)
    # 1 if yes (requires blank all-time, course records, aces, hall of champions, handicap, pool averages by round, and slope/rating sheet)
    # 0 if no
  # oldwb - if first = 0, the old workbook (ss=) from the previous season.
  # year - if first = 0, the year of the season that just finished (or season number, if that's how you've chosen to do it)
# output: base dataframes for each section of the league raw data

# Note: only needs to be done ONCE prior to any data being loaded in. Can just use bbround() after 1 round has been played.
# You also might need to check how the previous seasons were named, in case the name was changed during last season.

bbsetup <- function(link,first,oldwb,year)
{
  require(tidyverse)
  require(googlesheets4)
  #schedule
  schedule <- data.frame(
    Course = character(0),
    Layout = character(0),
    Datend = as.Date(character(0)),
    Date = character(0),
    StartTime = character(0),
    Note = character(0),
    RoundNo = numeric(0),
    AcePot = numeric(0),
    Par = numeric(0),
    ParFours = character(0),
    ParFives = character(0)
  )
  write_sheet(schedule,
              ss=link,
              sheet='League Schedule')
  
  #leaderboard
  leaderboard <- data.frame(
    Name = character(0),
    # Tag = integer(0),
    Points = integer(0),
    Rounds = integer(0),
    Handicap = character(0)
  )
  write_sheet(leaderboard,
              ss=link,
              sheet='Leaderboard')
  
  #handicap
  handicap <- data.frame(
    Name = character(0),
    Handicap = numeric(0)
  )
  write_sheet(handicap,
              ss=link,
              sheet='Handicap')
  
  #scores for the season (for recaps and pool assignment)
  allscores <- data.frame(
    Name = character(0)
  )
  write_sheet(allscores,
              ss=link,
              sheet='Full Season Scores')
  
  # create empty dataframes if first league season
  if(first == 1)
  {
    # course slopes and ratings
    slopedf <- data.frame(
      Course = character(0),
      Layout = character(0),
      Par = integer(0),
      TotRds = integer(0),
      StrokeTotal = numeric(0),
      ParTotal = numeric(0),
      GM = numeric(0),
      Rating = numeric(0),
      Slope = numeric(0),
      StdSlope = numeric(0),
      Weight = numeric(0)
    )
    write_sheet(slopedf,
               ss=link,
               sheet='Course Slopes and Ratings')
    
    # pool df (to be used when there's enough information)
    pools <- data.frame(
      Name = character(0),
      Pool = character(0)
    )
    write_sheet(pools,
                ss=link,
                sheet='Player Pool Assignments')
    
    # pool averages by round
    poolavg <- data.frame(
      RdNo = integer(0),
      Course = character(0),
      Layout = character(0),
      Par = integer(0),
      Pool = character(0),
      Players = integer(0),
      Strokes = integer(0),
      Avg = numeric(0),
      StdDev = numeric(0),
      ParStrokes = integer(0)
    )
    write_sheet(poolavg,
               ss=link,
               sheet='Poolwise Strokes by Round')
    
    # past all-time
    alltime <- data.frame(
      Name = character(0),
      Points = integer(0),
      Rounds = integer(0),
      Season = character(0)
    )
    write_sheet(alltime,
                ss=link,
                sheet='Past All Time')
    
    # current all-time
    current <- data.frame(
      Name = character(0),
      Seasons = integer(0),
      Rounds = integer(0),
      Points = integer(0),
      milestonen = integer(0)
    )
    write_sheet(current,
                ss=link,
                sheet='Current All Time')
    # aces
    aces <- data.frame(
      Name = character(0),
      Date = as.Date(character(0)),
      Course = character(0),
      Layout = character(0),
      Hole = character(0),
      Payout = numeric(0)
    )
    write_sheet(aces,
                ss=link,
                sheet='Aces')
    
    # course records
    records <- data.frame(
      Course = character(0),
      Layout = character(0),
      Name = character(0),
      Score = integer(0),
      Date = as.Date(character(0))
    )
    write_sheet(records,
                ss=link,
                sheet='Course Records')
    
    # hall of champions
    champs <- data.frame(
      Event = character(0),
      Year = integer(0),
      Division = character(0),
      Name = character(0),
      Score = character(0)
    )
    write_sheet(champs,
                ss=link,
                sheet='Hall of Champions')
  }
  # otherwise get links per function values and rewrite into new league sheet
  else
  {
    # course slopes and ratings
    slopedf <- read_sheet(ss=oldwb,
                          sheet='Course Slopes and Ratings')
    write_sheet(slopedf,
               ss=link,
               sheet='Course Slopes and Ratings')
    
    # pool df (to be used when there's enough information)
    pools <- read_sheet(ss=oldwb,
                        sheet='Player Pool Assignments') %>%
      select(Name,
             Pool=Pool25)
    write_sheet(pools,
                ss=link,
                sheet='Player Pool Assignments')
    
    # pool averages by round
    poolavg <- read_sheet(ss=oldwb,
                          sheet='Pool Avg and SD by Round')
    write_sheet(poolavg,
               ss=link,
               sheet='Poolwise Strokes by Round')
    
    # all-time in per-round form to start the season and the summarized current board that will update each round
    newat <- read_sheet(ss=oldwb,
                          sheet='Leaderboard') %>%
      transmute(Name,
                Points,
                Rounds,
                Season=year)
    oldat <- read_sheet(ss=oldwb,
                        sheet='Past All Time')
    at2 <- rbind(oldat,newat) %>%
      arrange(Name,Season)
    
    write_sheet(at2,
                ss=link,
                sheet='Past All Time')
    at3 <- at2 %>%
      rename(rds=Rounds,
             pts=Points) %>%
      group_by(Name) %>%
      reframe(Name,
              Seasons = n(),
              Rounds = sum(rds),
              Points = sum(pts)) %>%
      ungroup() %>%
      transmute(Name,
                Seasons,
                Rounds,
                Points,
                milestonen = NA) %>%
      distinct() %>%
      arrange(desc(Points),desc(Seasons))
    write_sheet(at3,
                ss=link,
                sheet='Current All Time')
    # aces
    aces <- read_sheet(ss=oldwb,
                       sheet='Aces')
    write_sheet(aces,
                ss=link,
                sheet='Aces')
    
    # course records
    records <- read_sheet(ss=oldwb,
                          sheet='Course Records')
    write_sheet(records,
                ss=link,
                sheet='Course Records')
    
    # hall of champions
    champs <- read_sheet(ss=oldwb,
                         sheet='Hall of Champions')
    write_sheet(champs,
                ss=link,
                sheet='Hall of Champions')
  }
}