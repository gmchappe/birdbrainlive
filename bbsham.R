# Birdbrain 2026 function script
# C GeoffStats LLC 2026
# function: bbsham()
# input: 
  # df - today's scores
  # pools - last season's pool assignments (take the prior totrds/stroketotal/partotal too)
  # pa - the by-row dataframe of average scores per round per pool
  # hcp - the current handicap adjustment board
  # sched - subset of full schedule from input[1]
  # col - the value "COLNAME" made in bbround() from the name and date on the schedule.
  # wb - the output file from runsheet/bbround()
# process: adds df to pa, then re-calculates slope if necessary to calculate adjustment. that then goes to output.
# output: dataframe of new calculated hcp

bbsham <- function(df,pools,pa,hcp,sched,col,wb)
{
  require(tidyverse)
  require(data.table)
  require(googlesheets4)
  
  # SLOPE CALCULATION
  cat(paste0("Recalculating grand slope...  "))
  
  res <- merge(x=df,y=pools,by=c("Name")) %>%
    filter(!is.na(Pool)) %>%
    group_by(Pool) %>%
    summarise(Players = n(),
              strk = sum(round_total_score),
              pstrk = sched$Par[1]*nrow(df),
              avg = mean(round_total_score),
              sd = sd(round_total_score)) %>%
    mutate(Strokes = sum(strk),
           Parstrokes = sched$Par[1]*Players,
           rtg = (Strokes-Parstrokes)/sum(Players))
  maxrd = max(pa$RdNo)
  parow <- res %>%
    transmute(Course = sched$Course[1],
              Layout = sched$Layout[1],
              RdNo = maxrd + 1,
              Par = sched$Par[1],
              Pool = as.factor(Pool),
              Players,
              Strokes = strk,
              totalpar = Par*Players,
              RoundNo = sched$RoundNo[1])
  
  # update pool avg (regardless of whether this is a new layout or not)
  newpoolavg <- rbind(parow,pa)
  write_sheet(newpoolavg,
              ss=wb,
              sheet = 'Poolwise Strokes by Round')
  # create npool
  newpoolavg2 <- newpoolavg %>%
    mutate(npool=case_when(Pool=='A'~1,
                           Pool=='B'~2,
                           Pool=='C'~3,
                           Pool=='D'~4,
                           Pool=='E'~5,
                           TRUE~NA))
  # calculate GRAND slope (independent of course)
  grandslope <- newpoolavg2 %>%
    group_by(Pool,npool) %>%
    summarise(rounds=sum(Players),
              score=sum(Strokes),
              par=sum(totalpar)) %>%
    ungroup() %>%
    mutate(absdiff=score-par,
             rtg=absdiff/rounds)

  model<-lm(rtg~Pool, data=grandslope)
  
  cat(paste0("The grand slope has been recalculated as ", as.numeric(model$coefficients[2]), " .\n"))
  
  # calculate new ratings for each course - same as above, just regrouping independent of pool.
  slopesheet <- newpoolavg2 %>%
    group_by(Course,Layout,Par) %>%
    mutate(layoutid = cur_group_id()) %>%
    ungroup() %>%
    group_by(layoutid,Course,Layout,Par,Pool,npool) %>%
    summarise(rounds=sum(Players),
              score=sum(Strokes),
              par=sum(totalpar)) %>%
    mutate(absdiff=score-par,
           rtg=absdiff/rounds) %>%
    filter(!is.na(Pool))
  
  dt <- data.table(slopesheet)
  
  dt2 <- dt[,as.list(coef(lm(rtg~npool))),by=layoutid] %>%
    rename(slope = npool,
           int=`(Intercept)`)
  
  lms <- merge(x=slopesheet,y=dt2,by=c('layoutid'),all=TRUE) %>%
    select(Course,Layout,Par,int,slope) %>%
    distinct()

  cat(paste0("The mean slope as distributed amongst the ", max(slopesheet$layoutid), " layouts incorporated is ", mean(lms$slope), ".\n"))
  
  # find course rating (i.e. average) relative to par
  preratings <- newpoolavg2 %>%
    group_by(Course,Layout,Par) %>%
    mutate(layoutid = cur_group_id()) %>%
    summarise(rounds = sum(Players),
              total = sum(Strokes),
              partotal = sum(totalpar)) %>%
    ungroup() %>%
    mutate(prerating = (total - partotal) / rounds,
           absdiff = (total - partotal)) %>%
    arrange(Course,Layout)
  
  # find total and GM rating relative to par (independent of course)
  # be able to append to slope
  gmslope <- newpoolavg2 %>%
    summarise(rounds = sum(Players),
              total = sum(Strokes),
              partotal = sum(totalpar)) %>%
    mutate(avgrtg = (total - partotal) / rounds) 
  
  grandmean <- gmslope %>% select(avgrtg)
  cat(paste0("The grand mean relative to par across all layouts is ", grandmean$avgrtg[1], "!\n"))
  
  #adjust rating by grand mean
  ratings <- as.data.frame(c(preratings,grandmean)) %>%
    mutate(Rating = prerating - avgrtg) %>%
    transmute(Course,Layout,Par,GM=avgrtg,Rating)
  
  lms2 <- merge(x=lms,y=newpoolavg2,by=c('Course', 'Layout'),all.x = TRUE)
  
  sloperating <- merge(x=ratings, y=lms2, by=c('Course', 'Layout'), all=TRUE) %>%
    transmute(Course, Layout, Par, Rating, Slope=slope, GM) %>%
    filter(!is.na(Rating))
  
  newslope <- merge(x=sloperating, y=lms2, by=c('Course', 'Layout'), all=TRUE) %>%
    transmute(Course, Layout, Par, 
              TotRds=gmslope$rounds,
              StrokeTotal=gmslope$total,
              ParTotal=gmslope$partotal,
              GM, 
              Rating, 
              Slope=slope,
              scale=as.numeric(scale(Slope)),
              Weight=case_when(scale >= 3 ~ 1.2,
                               scale >= 1 ~ 1.1,
                               scale >= 0.5 ~ 1.05,
                               scale >= -0.5 ~ 1,
                               scale >= -1 ~ 0.95,
                               scale >= -3 ~ 0.90,
                               TRUE ~ 0.80)) %>%
    rename(StdSlope=scale) %>%
    distinct()
  
  # update slope
  cat(paste0("Layouts re-rated.\n"))
  
  # create adjustment and rewrite handicap
  sloperow <- newslope %>%
    filter(Course == sched$Course[1] & Layout == sched$Layout[1])
  adjustment <- df %>%
    transmute(Name,
              "{col}" := (round_relative_score-sloperow$Rating[1])*sloperow$Weight[1])
  
  cat(paste0("Calculating handicaps...  "))
  newhcp1 <- merge.data.frame(hcp, adjustment, by="Name", all=TRUE) %>%
    select(-Handicap)
  for(i in 1:nrow(newhcp1)) {
    if(i==1){
      x <- newhcp1[i,] %>% select(-Name)
    } else {
      x <- newhcp1[i,] %>% select(-Name,-Handicap)
    }
    x2 <- as.data.frame(t(x))
    colnames(x2) = c('Score')
    x2 <- x2 %>%
      filter(!is.na(Score))
    rounds = as.integer(nrow(x2))
    cut = floor(rounds / 5)
    x3 <- x2 %>%
      arrange(Score) %>%
      mutate(maxrank = row_number(),
             minrank = rounds - row_number() + 1,
             cutflag = case_when(cut == 0 ~ 'N',
                                 TRUE ~ case_when(maxrank <= cut | minrank <= cut ~ 'Y',
                                                  TRUE ~ 'N'))) %>%
      filter(cutflag == 'N')
    newhcp = as.numeric(mean(x3$Score))
    newhcp1$Handicap[i] = newhcp
  }
  
  #update handicap
  write_sheet(newhcp1,
              ss= wb,
              sheet = 'Handicap')
  cat(paste0("Done.\n\n"))
  
  # return name and new handicap for leaderboard - numeric needed for Handicap and will change in main round function
  newhcp2 <- newhcp1 %>%
    transmute(Name,
              Handicapn=Handicap)
  
  x <- list(newslope,newhcp2)
  return(x)
}